# Altx-cpp

This project is the C++ implementation of the [Altx](https://github.com/halmosb/altx) Python package. The functionality of the code should be the same; however, this implementation will use many high-performance computing libraries (OpenMP, MPI, HIP) in C++.

The purpose of this document is to plan the development of the code: before the start of the development the development tools (vim, git, Github CI/CI, Google Tests, CMake, Docker) and libraries (OpenMP, MPI, HIP, HDF5) are defined.

## Contents

- [Goal of the code](#goal-of-the-code) -- the algorithm, and the three properties of it that
  drive everything below
- [Structure of the code](#structure-of-the-code) -- two orthogonal axes, the backend seam, the
  directory layout, the parallel decomposition
- [File formats](#file-formats) -- the ALTX file, normative, a contract with the Python
  implementation
- [Docker](#docker) -- the image matrix, tags, Python policy, editor and profiler setup
- [Pre-commit](#pre-commit) -- what is checked where, and how the documentation rules are enforced
- [Github CI/CD](#github-cicd) -- workflows, what runs when, runner constraints
- [High performance computing libraries](#high-performance-computing-libraries) -- HDF5, OpenMP,
  MPI, HIP, LAPACK/BLAS
- [Argument parser](#argument-parser), [Storing tensors](#storing-tensors),
  [Scalar type](#scalar-type)
- [Numerical policy](#numerical-policy-nan-is-not-a-legal-value) -- NaN is not a legal value
- [CMake](#cmake), [Google Tests](#google-tests)
- [Development stages](#development-stages) -- and what is testable without hardware

### The decisions that everything else follows from

If only a few things are read from this document, these are the ones that constrain the rest:

| Decision | Where |
| --- | --- |
| There are two orthogonal axes (backend, distribution), not three versions | [Structure](#two-orthogonal-axes-not-three-versions) |
| The backend is chosen at **run time**, so both live in one binary | [The backend seam](#the-backend-seam) |
| One HDF5 schema for input, model and output, shared with Python | [File formats](#the-altx-file-normative) |
| Laws are replicated, instances are distributed; no per-class communicator | [Parallel decomposition](#parallel-decomposition) |
| NaN is not a legal value anywhere in the pipeline | [Numerical policy](#numerical-policy-nan-is-not-a-legal-value) |
| The eigensolver is hand-written, not LAPACK, and the GPU backend uses no SOLVER library | [LAPACK, BLAS](#the-eigenproblem-lapack-is-the-wrong-tool-at-this-size) |
| Serial and OpenMP must produce **bitwise identical** output | [OpenMP](#the-same-code-should-also-mean-the-same-numbers) |

## Goal of the code
The code should implement the Altx algorithm by first extracting the laws from the training set, then it should transform the transform set.

Stated precisely, so that the rest of this document has something concrete to refer to. The data
is a set of instances, each holding `m` channels of time series. There are two phases.

**Train.** For each `(r, l, k)` triplet, slide a window of length `r` with step `k` over every
channel of every instance. Each window is sub-sampled with stride `step = (r-1)/(2l-2)` and
embedded into a symmetric `l x l` matrix `S`. The eigenvector belonging to the **smallest
absolute** eigenvalue of `S` is a *law*. The laws are collected into `P`, each tagged with the
class label of the instance it came from. A window whose eigendecomposition fails contributes no
law.

**Transform.** For a test instance, build the same windowed embedding and project it onto every
stored law, giving a matrix `M` per channel. Square it, split the law axis by class, and reduce
each class's block to scalars: a quantile along the law axis, then a statistic along the window
axis. The feature vector has length `len(RLK) * noc * n_methods * m`.

Three properties of this shape drive nearly every decision in this document:

- The train phase is a very large number of **independent, tiny** eigenproblems -- which is why it
  parallelises trivially and why LAPACK is the wrong tool for it.
- The transform phase is a **thin-`k` GEMM followed by a selection**, which is bandwidth-bound and
  whose intermediate result must never be fully materialised.
- The two phases share nothing but `P`, which is **small**. That is what allows the laws to be
  replicated across MPI ranks and the whole load-balancing problem to disappear.

## Structure of the code

### Two orthogonal axes, not three versions

The four runtime images listed under [Docker](#docker) are not four implementations of the
algorithm; they are the cells of a 2x2 matrix over two independent axes:

|                                    | no MPI | MPI            |
| ---------------------------------- | ------ | -------------- |
| **CPU** (serial = OpenMP disabled) | yes    | yes            |
| **GPU** (HIP)                      | yes    | yes (later)    |

1. **Compute backend** -- where the arithmetic happens: CPU or HIP device. OpenMP is *not* a
   backend, it is a compile option inside the CPU backend. The `#pragma omp` directives compile
   to nothing when OpenMP is disabled, so the serial and the OpenMP builds share one source file
   with no `#ifdef`s, as required above.
2. **Distribution** -- how the work is split across processes: none or MPI. This is a
   `Communicator` abstraction whose serial implementation is a no-op returning `rank = 0` and
   `size = 1`. The single-process build therefore executes the *same* code path as the MPI build,
   which means the decomposition logic can be tested without `mpirun`.

There is exactly one implementation of the algorithm. An `#ifdef` that selects between algorithm
variants is a design failure, not a build configuration.

### The backend seam

The algorithm has three compute-heavy stages, and these are the virtual boundaries:

| Stage                                          | Work                                              | Cost centre           |
| ---------------------------------------------- | ------------------------------------------------- | --------------------- |
| `extractLaws(dataset, rlk) -> P, P_classes`    | many tiny `l x l` symmetric eigenproblems         | training bottleneck   |
| `project(instance, P, rlk) -> M`               | one GEMM per channel                              | transform bottleneck  |
| `extractFeatures(M, P_classes, methods) -> f`  | square, quantile over the law axis, the statistic | selection over a long axis |

The backend is selected at **runtime** (`--device cpu|gpu`) through an abstract `Backend`
interface with those three methods. The virtual boundary is crossed once per
*(rlk triplet, instance)* -- a few thousand times per run, with millions of floating point
operations behind each call -- so the dispatch cost is unmeasurable. In exchange there is a single
binary per image, the CLI mirrors the `device` parameter of the Python implementation, and an
integration test can push identical input through both backends and diff the result. That
cross-backend diff is the cheapest way to keep the GPU port honest.

Each backend owns its own allocations, and the interface is expressed in terms of buffers that the
backend handed out, with explicit upload and download. No host-device copy may happen between
`extractLaws` and `project`.

### `LawExtractor` and `Transformer`

The Python `Altx` class is deliberately *not* ported as a single class. In Python, `load()`
constructs an object through `__new__` without a `train_set`, so a later `train()` call fails at
run time. Because the C++ CLI must consume models trained elsewhere -- including models trained by
the Python implementation -- the responsibility is split in two:

- **`LawExtractor`** -- owns the training data, produces the laws.
- **`Transformer`** -- owns the laws and the parameters, consumes the test data. It never holds
  training data.

`altx train` produces a `Transformer`; `altx transform` constructs one directly from the `/laws`
group of an existing ALTX file. The "cannot train after loading" failure mode ceases to exist
instead of being documented.

### Command line interface

```
altx train      --input <in.h5|csv|arff>  --output <out.h5>  -R ... -L ... -K ...
                [--save-laws] [--save-input]
altx transform  --input <in.h5|csv|arff>  --laws-from <run.h5>  --output <out.h5>
                [--extract mean:0.05 ...] [--csv <out.csv>] [--retain laws,data] [--append]
altx run        # train and transform in one process, without an intermediate file
```

There is one file format for all of these, described under [File formats](#file-formats).
`--laws-from` and `--input` may name the same file, since a single ALTX file can carry both the
laws and the data.

### Directory layout

Because the deliverable is a CLI tool and not a library, there is no installed public header set
and therefore no `include/` and `src/` mirror to keep synchronised. Headers live next to their
sources. The code is still built as a static library so that the application and the test binaries
link the same objects, but that library is internal and is never installed.

```
Altx-cpp/
|-- CMakeLists.txt              # options: ALTX_ENABLE_{OPENMP,MPI,HIP,TESTS}, ALTX_SCALAR
|-- CMakePresets.json           # named configurations = the Docker target matrix
|-- cmake/                      # find modules, warnings, sanitizers, coverage, clang-format
|-- src/
|   |-- core/                   # backend agnostic; knows nothing about MPI or HIP
|   |   |-- Tensor.hpp          #   allocator-policy tensor, raw pointer, manual indexing
|   |   |-- RLK.hpp             #   the (r, l, k) triplet and its validation rules
|   |   |-- Parameters.hpp      #   the class holding all program parameters
|   |   |-- Version.hpp.in      #   configure_file: version string and full git hash
|   |   |-- Exceptions.hpp
|   |   `-- Logger.hpp          #   rank-aware output
|   |-- io/
|   |   |-- DataSet.hpp         #   train set, classes, lengths
|   |   |-- Reader.hpp          #   + CsvReader, ArffReader, H5Reader
|   |   |-- Writer.hpp          #   + CsvWriter, H5Writer (serial and parallel: one class)
|   |   `-- AltxFile.hpp        #   read/write the single ALTX file schema specified below
|   |-- backend/
|   |   |-- Backend.hpp         #   the abstract seam
|   |   |-- cpu/CpuBackend.*    #   OpenMP pragmas live here
|   |   |-- cpu/LinAlg.hpp      #   BLAS/LAPACK wrapper, the one templated component
|   |   `-- hip/HipBackend.*    #   compiled by hipcc; rocBLAS / hipBLAS
|   |-- dist/
|   |   |-- Communicator.hpp    #   abstract
|   |   |-- SerialComm.hpp      #   no-op, size = 1, the default
|   |   `-- MpiComm.hpp
|   |-- LawExtractor.{hpp,cpp}  # train
|   `-- Transformer.{hpp,cpp}   # transform
|-- apps/altx_main.cpp          # one main, argparse subcommands
|-- tests/{unit,integration,fixtures}/
|-- benchmarks/                 # the synthetic dataset harness of development stage 3
|-- docker/                     # dev-cpu, dev-gpu, run-{cpu,mpi,hip,mpi-omp}
|-- docs/                       # Doxyfile.in, mainpage.md, DevelopmentEnvironment.md
|-- scripts/                    # gen_reference.py, build_docker_images_locally.sh, checks
|-- external/                   # argparse and HOP, fetched by CMake
`-- .github/workflows/
```

**Invariant:** `src/core/` must not know about MPI or HIP. It compiles identically in all four
configurations. Needing an `#ifdef ALTX_HAS_MPI` inside `core/` is the signal that a
responsibility has been placed in the wrong layer.

### Parallel decomposition

The load balancing concern raised under [MPI](#mpi) is avoidable, and no per-class communicator is
needed:

- **Training** is embarrassingly parallel over *windows*, and the total number of windows is known
  exactly in advance from `train_length` and the `(r, k)` pair. The global window index space can
  therefore be split statically and perfectly evenly. Because the split is not by class, an
  imbalanced class distribution is irrelevant. Each rank ends up owning a slice of the laws, and
  each law carries its class label as data.
- **Transforming** needs all the laws, because the quantile in the feature extraction runs over
  the law axis within one class, and a distributed selection would be painful. The laws are small:
  for `tau = 1000`, `T = 500`, `K = 1`, `l = 4` there are roughly 500k laws, so
  `m * l * n_laws * 8` bytes is about 16 MB. The laws are therefore **allgathered after training
  and replicated**, and the *test instances* are distributed instead. Every rank then computes
  complete, local feature vectors and writes its own rows through parallel HDF5.
- The law storage stays behind a `LawStore` type, so that a distributed implementation can be
  substituted later if a dataset ever makes replication infeasible, without touching the
  algorithm.

## File formats

### The ALTX file (normative)

There is **one** HDF5 schema, not a separate model file and feature file. A single file holds the
parameters, and optionally the laws, the input data, the intermediate multiplied matrices, and the
extracted features. The same schema serves all three roles:

- as the **output** of a run,
- as the **model** for a later run, when it contains the laws,
- as the **input data** for a later run, when it contains the data.

A file produced by one run can therefore be fed straight back into the next, and a single path may
legitimately be given as both the law source and the data source.

The schema is a **bidirectional contract between the C++ and the Python implementation**: C++ must
read files written by Python and write files that Python can read. (The Python reader will be
added later, but files written by C++ must already make it possible.) The layout is therefore
specified normatively here and carries a `format_version` attribute from the first release. Any
change requires a version bump and an entry in this section.

```
/                                       attributes
    format_version    int32             1
    created_by        string            "altx-cpp 0.1.0" | "altx-py 0.4.2"
    git_hash          string            full 40 character hash of the writing implementation
    contents          string            comma separated list of the groups present
/parameters                             ALWAYS -- the full parameter set, see below
    m                 int32             number of channels
    noc               int32             number of unique classes
    class_labels      (noc,)    int32   sorted unique class labels
    rlk               (n_rlk,3) int32   rows are (r, l, k); this dataset defines the ordering
    ...                                 argv, extraction methods and quantiles, device, ...
/laws                                   OPTIONAL -- --save-laws, default ON for `train`
    /000                                attributes: r, l, k, n_laws (int32)
        P             (m, l, n_laws)    float32 | float64
        P_classes     (n_laws,) int32   the class label of each law
    /001                                ...
/data                                   OPTIONAL -- written with --save-input
    /train                              attributes: tau, m, T (int32)
        X             (tau, m, T)       float32 | float64, padded
        lengths       (tau,)    int32
        classes       (tau,)    int32
    /test                               same structure, present when a transform ran
/multiplied                             OPTIONAL -- --save-multiplied, debug aid, see warning
/features                               OPTIONAL -- present when a transform ran
    /000                                attributes: n_instances, n_features
        F             (n_instances, n_features)   float32 | float64, chunked by rows
        classes       (n_instances,)    int32
        columns       (n_features,)     string, the header names
        methods       string            the extraction methods that produced these columns
    /001                                ...
```

**A file is usable as a model if and only if `/laws` is present and complete.** The reader
validates this and fails with a clear message rather than a missing-dataset error. `/parameters`
is the only mandatory group.

`--save-laws` defaults **on** for `altx train`, since the laws are the only product of a training
run, and off for `altx run`. `--save-input` and `--save-multiplied` default off everywhere.

> **Warning:** `/multiplied` holds `nol_tilde * n_laws * m` values *per instance*. For the 500k-law
> example under [Parallel decomposition](#parallel-decomposition) that is gigabytes for a single
> instance. It is a debugging aid for small inputs; a plain flag is accepted for now, but writing
> it on a real dataset will fill the disk.

Rules:

1. **The law groups are indexed (`000`, `001`, ...), not named after their triplet.** HDF5
   iterates group members alphabetically by default, so a name such as `r10_l6_k1` would sort
   before `r5_l3_k1` and silently permute the feature vector columns. The `rlk` dataset is the
   authoritative ordering; the group names are only slots. Group `NNN` corresponds to row `NNN` of
   `rlk`.
2. **`P` is stored as `(m, l, n_laws)`**, that is, with the channel index outermost. This differs
   from the `(l, n_laws, m)` layout of the Python implementation, and the difference is a
   performance decision rather than a cosmetic one. The projection is, for each channel `j`,
   `data[:, :, j] @ P[:, :, j]`. In a row-major `(l, n_laws, m)` array that submatrix has row
   stride `n_laws * m` and column stride `m`; neither is unit, so it cannot be passed to a BLAS
   `gemm` without a copy. In `(m, l, n_laws)` each channel's laws form a contiguous `l x n_laws`
   matrix with `ldb = n_laws`, which feeds `gemm` directly, and on the GPU it becomes a single
   strided batched GEMM with `batch = m` and clean strides. The same principle -- **channel index
   outermost** -- applies to the internal layout of the embedded data, because every stage of the
   algorithm is independent per channel. On the Python side the conversion is one
   `P.permute(2, 0, 1).contiguous()` at save time.
3. **The dtype is a property of the dataset, and the reader converts.** A `double` build must be
   able to read a `float32` file written by torch, and vice versa. Passing the in-memory scalar
   type to `H5Dread` makes HDF5 perform the conversion, so no special handling is required. This
   is one of the reasons the scalar type can be a typedef rather than a template parameter.
4. **Only valid laws are stored.** The Python implementation uses `NaN` in a float
   `P_classes` tensor as an "unfilled" sentinel and compacts at the end of `_get_P`; only the
   compacted result reaches the file, and in the file `P_classes` is an integer dataset.
5. **Integer widths are pinned to `int32`** so that the file does not depend on whether numpy chose
   `int32` or `int64` on the writing platform.
6. **Strings are variable-length UTF-8**, which is what `h5py` produces and consumes naturally.
7. **Endianness** is written as native little-endian; HDF5 converts on read, so no byte swapping
   is needed in either implementation.
8. **`/laws` and `/parameters` are contiguous; `/data` and `/features` are chunked**, the latter
   by instance rows, so that MPI ranks can write their own rows collectively. Compression is
   available on `/data`, but note that collective writes to a compressed dataset are restricted in
   HDF5, so compression and parallel output are not freely combinable.
9. **All ranks must agree on which optional groups exist**, because dataset creation in parallel
   HDF5 is a collective operation. The set of groups is derived from the parameters, which every
   rank has, so this is satisfied by construction.
10. **Instances are padded, never ragged.** `/data/*/X` is a padded `(tau, m, T)` dataset with a
    companion `lengths` vector, matching the `train_length` and `test_length` semantics of the
    Python implementation.

A note for whoever modifies the Python implementation: `P[:, mask, :]` produces a non-contiguous
view, so it needs `.contiguous()` (which the permute in rule 2 provides anyway) before `h5py`
writes it.

The extraction methods and their quantiles are recorded under `/parameters` as **provenance**, so
that a feature header can be reconstructed from the file alone. They remain overridable on the
`transform` command line; the recorded values are not binding.

### Consistency checks on read

Because one file can now carry laws, data and parameters that were written at different times, the
reader validates their mutual agreement before doing any work, and reports which two things
disagree:

- `m` of `/laws` matches `m` of `/data` and of `/parameters`;
- every `n_laws` and `l` matches the corresponding row of `/parameters/rlk`;
- every value in `P_classes` appears in `class_labels`;
- each `(r, l, k)` still satisfies `(r - 1) % (2*l - 2) == 0`;
- `format_version` is supported.

### Writing: new file by default, append by request

Reading a file and writing into it in place is possible in HDF5 but risks corrupting the source,
and is worse under MPI. The default is therefore to **open the input read-only and always write a
new output file**, copying forward whatever the user asks to retain (`--retain laws,data`).

The `save_file_mode` of the Python implementation maps onto this as follows:

| Python mode       | ALTX file equivalent                                              |
| ----------------- | ----------------------------------------------------------------- |
| `New file`        | the default: a fresh output file                                   |
| `Append instance` | extend the row axis of an existing `/features/NNN` (needs `--append`) |
| `Append feature`  | add a **new** `/features/NNN` group                                |

Making "append feature" a new indexed group rather than an extension of the column axis avoids a
dataset that must be extendible in two dimensions, and it preserves which extraction methods
produced which columns -- information the Python CSV path loses. Consumers that want the Python
behaviour concatenate the groups in index order.

## Docker
The docker images should be build using GIthub CI/CD and should be stored in Github Docker Register.

There should be different images for the different version of the code.
There should be 2 different images: for development and for running.
There should 2 development packages:
- CPU only: OpenMP, MPI, BLAS, LAPACK, HDF5, debuggers, profilers.
- GPU: OpenMP, MPI, BLAS, LAPACK, HDF5, debuggers, profilers.

### Image matrix

A runtime image is defined by the **shared libraries it must carry**, not by the build flags of
the binary inside it. This collapses the originally planned four runtime images:

- "Serial / OpenMP" and "OpenMP / MPI" and "MPI" do not need three images. Serial and OpenMP have
  identical runtime dependencies (`libgomp` is present either way), and the two MPI variants
  differ from each other only in a compile flag.
- Therefore: **serial and OpenMP ship as two binaries in one image**, which is also what
  development stage 4 needs, since an honest serial-versus-OpenMP profiling comparison requires a
  genuinely non-OpenMP binary rather than `OMP_NUM_THREADS=1`.

The resulting set is three runtime images and two development images:

| Image           | Runtime libraries                                   | Binaries                     |
| --------------- | --------------------------------------------------- | ---------------------------- |
| `runtime-cpu`   | OpenBLAS, LAPACK, serial HDF5, libgomp              | `altx-serial`, `altx-omp`    |
| `runtime-cpu-mpi` | + OpenMPI, parallel HDF5                          | `altx-mpi`, `altx-omp-mpi`   |
| `runtime-gpu`   | + ROCm runtime, rocBLAS, rocSOLVER                  | `altx-hip`, `altx-hip-mpi`   |
| `dev-cpu`       | everything above minus ROCm, plus the toolchain      | --                           |
| `dev-gpu`       | `dev-cpu` plus the ROCm SDK and `hipcc`             | --                           |

MPI is kept in its own image because it is the dependency with a **host compatibility
constraint** -- clusters frequently require the container MPI to match the site MPI -- so it is
the one that will need rebuilding per deployment. The GPU image absorbs its MPI variant instead of
splitting further, because `libmpi` is negligible next to a multi-gigabyte ROCm layer.

**Not all five images are built at once.** There is no target cluster yet, and the first milestone
is a working serial and OpenMP version, so the order is:

1. `dev-cpu` and `runtime-cpu` -- everything needed for stages 1 to 4.
2. `runtime-cpu-mpi` -- when stage 5 starts. Since the MPI flavour of a future cluster is unknown,
   this image is built against the distribution OpenMPI and treated as **provisional**; the MPI
   flavour stays a base-image `ARG` precisely so it can be re-pointed later without touching
   anything else.
3. `dev-gpu` and `runtime-gpu` -- when stage 6 starts, and only if AMD hardware is available.

Writing the full matrix down now is still worthwhile, because it is what keeps the CMake options
orthogonal. Building it all now is not.

Note that the binary names describe the **build configuration**, not the device: because the
backend is selected at run time, `altx-hip` also runs on the CPU with `--device cpu`. That is
exactly what makes the cross-backend diff test possible.

### Build structure

```
base-cpu   (runtime libraries only, pinned by digest)
 |-- dev-cpu       (+ compilers, CMake, gdb, Score-P, vim/clangd, doxygen, lcov, python)
 |    `-- dev-gpu  (+ ROCm SDK, hipcc)
 `-- runtime-*     (multi-stage: FROM dev-* AS builder, then COPY --from into base)
```

The runtime images are produced by **compiling inside the development image and copying the
binaries into the base image**. This is the point of the layering: it makes "builds in the dev
container" and "builds in CI" the same statement by construction, rather than two things that
drift apart.

```
docker/
|-- base.Dockerfile       # ARG FLAVOR=cpu|gpu
|-- dev.Dockerfile
|-- runtime.Dockerfile    # multi-stage
|-- vim/                  # vimrc, pinned vim-plug list, clangd config
`-- scripts/              # entrypoints, MPI wrapper
```

The same images are built locally by `scripts/build_docker_images_locally.sh`, so that the CI
workflow is a second consumer of the Dockerfiles rather than the only one. It builds the same
three-layer chain in the same order, with the same build arguments, and can run a smoke check on
each result -- catching "works in CI only" before it becomes a debugging session in Actions.

Base images are pinned by **digest**, but individual apt package versions are not: Ubuntu drops
old versions from the archive, so pinning them turns every base refresh into a maintenance task
for very little additional reproducibility.

### Presets map one-to-one onto images

`CMakePresets.json` and the image matrix are one artifact. Every preset names the image it is
built in, so a configuration that CI builds is always a configuration a developer can reproduce.

| Preset                | Image             | OPENMP | MPI | HIP | Build type          |
| --------------------- | ----------------- | ------ | --- | --- | ------------------- |
| `cpu-serial-release`  | `runtime-cpu`     | OFF    | OFF | OFF | Release             |
| `cpu-omp-release`     | `runtime-cpu`     | ON     | OFF | OFF | Release             |
| `cpu-mpi-release`     | `runtime-cpu-mpi` | OFF    | ON  | OFF | Release             |
| `cpu-omp-mpi-release` | `runtime-cpu-mpi` | ON     | ON  | OFF | Release             |
| `gpu-hip-release`     | `runtime-gpu`     | ON     | OFF | ON  | Release             |
| `gpu-hip-mpi-release` | `runtime-gpu`     | ON     | ON  | ON  | Release             |
| `*-debug`             | `dev-*`           | as above     |     |     | Debug + sanitizers  |
| `coverage`            | `dev-cpu`         | ON     | OFF | OFF | Debug + gcov        |

### Library choices

- **BLAS: OpenBLAS by default, MKL behind a build ARG.** The earlier `ALT-CPP` defaulted to MKL
  with hardcoded `/opt/intel/oneapi` paths. Since the GPU target is AMD, the CPUs are likely AMD
  too, where MKL has historically dispatched to slower code paths. AMD AOCL/BLIS is the
  alternative worth benchmarking in development stage 4.
- **HDF5: distribution packages for both flavours.** Ubuntu installs serial and parallel HDF5 side
  by side under `hdf5/serial` and `hdf5/openmpi`, and CMake selects between them with
  `HDF5_PREFER_PARALLEL`. This removes the custom `/opt/hdf5-parallel` build that `ALT-CPP`
  needed.
- **MPI: OpenMPI from the distribution** for development convenience. Cluster deployment may
  require rebuilding against the site MPI, which is why the MPI flavour is a base-image ARG.
- **GPU base: `rocm/dev-ubuntu-24.04` for AMD.** See the NVIDIA caveat below.

### NVIDIA support via HOP

The code is written in HIP and ported to CUDA only if needed, using
[cschpc/hop](https://github.com/cschpc/hop) ("Header Only Porting", MIT licensed, from CSC).
HOP redefines identifiers at preprocessing time and intercepts the include statements, so the port
is a matter of compile flags rather than source changes:

```
# build the HIP sources for CUDA
-x cu -I$HOP_ROOT -I$HOP_ROOT/source/hip -DHOP_TARGET_CUDA
```

Consequences for this project:

- **HOP is vendored under `external/`** alongside `argparse`. It ships no CMake package, so a
  small `INTERFACE` target in `cmake/` supplies the include directories, the `HOP_TARGET_*`
  definition and the `-x` language flag.
- **HOP covers the runtime API, BLAS, FFT, RAND, RTC and SPARSE, but there is no SOLVER header.**
  This does not block us, because of a decision already taken for performance reasons: the
  training step is a very large number of *tiny* `l x l` symmetric eigenproblems with `l` in the
  range 3 to 8, which is served far better by a hand-written Jacobi kernel with one thread per
  matrix than by a batched rocSOLVER call. The GPU backend therefore needs only the runtime API
  and a batched GEMM for the projection, and both are inside HOP's coverage. **Do not introduce a
  hipSOLVER dependency**, or the CUDA port stops being a flag change.
- **HOP gives API portability, not performance portability.** Wavefront width differs (64 on AMD,
  32 on NVIDIA) and occupancy tuning will not transfer. The one-thread-per-matrix Jacobi kernel is
  largely insensitive to this, which is convenient, but the projection and the feature extraction
  will still need retuning if CUDA becomes a real target.
- Its documented weak spot is source files that do not include the GPU headers explicitly, so the
  backend sources must include them rather than relying on transitive includes.

A native `hip-runtime-nvidia`-on-CUDA base image is therefore **not** planned. If CUDA is ever
needed, it is a new preset and a new image built from the same sources.

### Managing image tags

The scheme follows [`docker-builder`](https://github.com/halmosb/docker-builder): every git tag
produces a matching image tag, alongside a rolling tag, with a registry build cache and a Trivy
scan. Two things are added, because `docker-builder` builds **only** on `v*` tags and that is not
enough here: a development image that tracks the newest commit, and a content-addressed tag that
keeps CI from rebuilding that image on every push.

**One GHCR package per flavour**, with the version alone in the tag:

```
ghcr.io/<owner>/altx-cpp/dev-cpu:v1.2.3
ghcr.io/<owner>/altx-cpp/runtime-cpu:v1.2.3
```

rather than `docker-builder`'s single package with the variant folded into the tag
(`.../python:3.14-cpu-v0.2.0`). One package per flavour gives per-flavour retention rules, a
separate Trivy category in the Security tab, a `latest` that means something per flavour, and --
most usefully -- it makes registry cleanup a generic per-package rule instead of the
variant-prefix regular expression that `docker-builder`'s `finalize` job has to maintain.

| Tag               | Written on                       | Mutable | Audience                     |
| ----------------- | -------------------------------- | ------- | ---------------------------- |
| `vX.Y.Z`          | push of git tag `v*`             | no      | releases, reproducibility    |
| `latest`          | push of git tag `v*`             | yes     | humans, "give me the release"|
| `edge`            | push to `main`                   | yes     | **humans: newest dev image** |
| `sha-<short>`     | every build                      | no      | debugging a specific build   |
| `hash-<content>`  | when the image inputs change     | no      | **CI jobs**                  |

The last two rows are the point:

- **`edge` answers "I want the most recent development image."** `docker pull …/dev-cpu:edge` is
  the everyday command, and a `main` build refreshes it.
- **`hash-<content>` answers "CI must not rebuild the dev image on every commit."** The tag is a
  hash of everything the image depends on: `docker/`, the base image digest and the package list.
  The workflow computes the hash, asks the registry whether that tag exists, and **skips the build
  entirely if it does**. A dev image build is minutes; a per-commit rebuild would dominate CI.

These do not conflict: the same build pushes `edge`, `sha-…` and `hash-…` at once, which costs
nothing because they are all one manifest. Humans follow the moving tag, CI pins the immutable
one. CI must never reference `edge` or `latest` -- that is exactly the drift the layered build
structure exists to prevent.

Release builds additionally **pin by digest** (`@sha256:…`).

### Registry hygiene

Copied from `docker-builder`: a **single-writer `finalize` job** performs deletions, so parallel
matrix jobs cannot race each other. Beyond that:

- A scheduled weekly workflow prunes untagged versions, which GHCR otherwise accumulates forever.
- `hash-…` tags are garbage: prune those not referenced by any workflow file, older than 90 days.
- `sha-…` tags are pruned after 30 days. `vX.Y.Z` tags are never deleted.

> **Check before copying:** `docker-builder`'s cleanup job calls
> `/orgs/{owner}/packages/container/…`. That endpoint is for **organisation**-owned packages. If
> the account is a personal one, the calls need `/user/packages/…` (delete) and
> `/users/{owner}/packages/…` (list) instead, and the existing job may be silently failing.

### Python in the images: light only, installed with uv

The development images carry a **light** Python: enough for `pre-commit` and small helper scripts,
and nothing more. The hard rule is **no pytorch**, because it is several hundred megabytes of
wheel in an image that is otherwise pulled constantly.

That rule decides where the fixture generator runs. `scripts/gen_reference.py` needs the Python
`altx` and therefore torch, so **it runs in the existing
[`docker-builder`](https://github.com/halmosb/docker-builder) image**,
`ghcr.io/halmosb/docker-builder/python:3.14-cpu`, which already exists for exactly this kind of
job. Nothing else in the C++ pipeline needs it: the fixtures are committed, and regenerating them
is a rare, deliberate act rather than part of any build.

For the light Python that does live in `dev-cpu` and `dev-gpu`, the answer to
**uv or micromamba is uv**:

- A single static binary, added with
  `COPY --from=ghcr.io/astral-sh/uv:<pinned> /uv /usr/local/bin/uv`. No shell hook, no activation,
  nothing to source in a `RUN` layer -- which is where conda-style tools cost the most inside a
  Dockerfile, and the cost is pure overhead when the requirement is "pre-commit and a few
  scripts".
- Fast enough that the layer stops being something to think about.
- `UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy`, a **pinned** uv version rather than `latest`, and a
  lock file, so the image is reproducible.

**micromamba is the better tool for a job this project does not have.** Its real advantage is
conda-forge's handling of native libraries -- an `h5py` genuinely built against the same MPI and
HDF5 that the C++ links, instead of the PyPI wheel's bundled serial HDF5. That matters when Python
and C++ must share native libraries inside one process. Here they never do: they communicate only
by exchanging `.h5` files as separate processes. So micromamba's strength goes unused while its
activation machinery is paid for in every layer. Worth revisiting only if that changes.

One practical detail: `pre-commit` builds and caches an environment per hook, so the image should
run `pre-commit install-hooks` at build time to bake `~/.cache/pre-commit` into a layer.
Otherwise the first commit in a fresh container pays for every hook environment at once.

### Vim

The right mechanism is **clangd driven by `compile_commands.json`**, which CMake emits with
`CMAKE_EXPORT_COMPILE_COMMANDS=ON`. Autocomplete and diagnostics for OpenMP, MPI, HDF5 and BLAS
then work with no per-library configuration, because clangd sees the real include paths and
defines of the actual build. **Vim 9 with `vim-lsp` is the editor configuration**, shipped as
`docker/vim/`.

Neovim with its built-in LSP client would need no plugins at all, and was the earlier proposal.
Vim is chosen instead for consistency with the
[`docker-builder`](https://github.com/halmosb/docker-builder) images, which are already part of
the daily workflow and already carry a `.vimrc` and vim-plug: one editor to configure and one set
of habits, rather than two. The price is four plugins where Neovim needs zero --
`prabirshrestha/{async.vim,vim-lsp,asyncomplete.vim,asyncomplete-lsp.vim}`, since `vim-lsp` alone
supplies only an `omnifunc` and not an as-you-type completion popup. All four are **pinned by
commit**, or the image stops being reproducible and the `hash-<content>` tag becomes a lie.
`vim-lsp-settings` is deliberately *not* used: it downloads language servers at run time, which
contradicts a pinned image where `clangd` comes from apt.

Two details decide whether this works at all, and both are invisible until they fail:

- **`--query-driver`.** `clangd` is clang, the build is GCC. Without
  `--query-driver=/usr/bin/g++*,/usr/bin/gcc*` clangd guesses where `libstdc++` lives; when the
  guess is wrong every line is red with `'bits/c++config.h' file not found`.
- **A fallback for when there is no compile database.** `compile_commands.json` does not exist
  until CMake has configured, so the image also ships `~/.config/clangd/config.yaml` adding
  `-std=c++20 -fopenmp -I/usr/include/hdf5/serial`. Of the four CPU libraries only serial HDF5
  needs an explicit include path -- OpenBLAS and LAPACKE resolve through
  `/usr/include/x86_64-linux-gnu` and `/usr/include`, and `omp.h` comes with GCC behind
  `-fopenmp`. These flags are additive and harmless once a real compile database takes over.

Because six presets mean six build directories, the compile database is selected explicitly by a
committed repo-root `.clangd` (`CompilationDatabase: build/cpu-omp-debug`) rather than by a
symlink that whichever `cmake --preset` ran last happens to win.

The one thing that needs deliberate handling: **clangd does not understand `hipcc`.** Entries for
`.hip` files must either be rewritten to `clang` with the HIP flags, or clangd must be given
`--query-driver`. Without this, the GPU sources are the only part of the codebase with no editor
support, which is exactly where it would be missed most.

The same image is used for VS Code through a `.devcontainer/devcontainer.json`, so both editors
resolve symbols identically.

### Debuggers and profilers

There should be debuggers and profilers in the development images:

- **gdb** -- from apt, no complications.
- **Score-P** -- not packaged for Ubuntu; it must be built from source against `binutils-dev`,
  `libunwind`, PAPI and the image's MPI. This is a slow source build, so it belongs in its own
  build stage whose result is copied in, keeping it out of the iteration path.
- **Intel VTune** -- available only through the Intel oneAPI apt repository, roughly 2--3 GB, and
  it needs relaxed `perf_event_paranoid` plus `CAP_PERFMON` on the host, so it cannot be assumed
  to work in every environment. Several of its features are Intel-only and therefore of limited
  value on AMD hardware. It goes into the development image behind `ARG WITH_VTUNE=0` so the
  default image stays lean, with `perf` and AMD uProf as the AMD-side alternative.
- **rocprof / omniperf** -- included with ROCm in `dev-gpu`, for the HIP work in stage 6.

### Vulnerability scanning

Images are scanned with Trivy in CI. Runtime images **fail** the build on HIGH or CRITICAL
findings; development images are **report-only**, since compilers, debuggers and VTune guarantee
a permanent backlog of findings that would otherwise block all work.

Size expectation: `runtime-cpu` in the low hundreds of MB, `runtime-gpu` several GB. The ROCm
layer dominates and is reduced by installing the ROCm *runtime* rather than the full SDK in the
runtime image -- the SDK belongs only in `dev-gpu`.

## Pre-commit
It should be enforced that the code is formated with clang-format and the documentation, which will be generated with Doxygen, in the code should be checked:
- Every function/class has documentation.
- Every argument is documented.
- Other linting (if possible) e.g. variable cases...

The question mark can be dropped: the **`pre-commit` framework itself is language-agnostic**. It
is a git hook runner that happens to be written in Python, and it drives hooks in any language,
including plain `system` hooks. The same `.pre-commit-config.yaml` structure as the Python
repository carries over directly, with C++ hooks substituted for black, ruff and mypy. The
framework needs a Python interpreter in the development image; that is the *only* reason one is
there, and it is kept light -- see
[Python in the images](#python-in-the-images-light-only-installed-with-uv). Note that the hooks
supply their own tools, so this stays light: the `clang-format` PyPI package ships a binary wheel,
and the hadolint, actionlint and shellcheck hooks fetch their own binaries.

### Three tiers, split by what a check needs

The organising principle is **what a check requires in order to run**, because that determines
where it can live without making commits slow or unreliable:

| Tier         | Requirement                          | Examples                                     |
| ------------ | ------------------------------------ | -------------------------------------------- |
| `pre-commit` | nothing but the changed files        | clang-format, whitespace, hadolint, actionlint |
| `pre-push`   | the whole tree, still local          | Doxygen documentation coverage                 |
| CI           | a **configured build**               | clang-tidy, `-Wdocumentation`, coverage        |

The middle column is the reason clang-tidy is **not** a pre-commit hook. clang-tidy needs
`compile_commands.json`, which only exists once CMake has configured a build directory. A hook
that silently skips when the file is missing gives different results to different developers,
which is worse than not having the check. clang-tidy therefore runs in CI, where the build is
guaranteed, and locally on demand through a CMake target.

Doxygen sits between the two: it needs no build, but it must see the whole tree, so it is too slow
for every commit and belongs at `pre-push`. `pre-commit` supports this directly with
`stages: [pre-push]`, so it is still one configuration file and one tool.

### Hooks

Carried over from the Python repository: `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`,
`check-json`, `check-merge-conflict`, `no-commit-to-branch` (`main` and `master`), and
`check-added-large-files`.

> `check-added-large-files` matters more here than it did in Python. The plan commits `.h5`
> fixtures for the cross-language round-trip test, so the limit is the thing standing between the
> repository and someone committing a multi-gigabyte dataset. The Python repository allows
> `--maxkb=5000`; **500 is more appropriate** for fixtures that were deliberately chosen to be
> small.

Added for this project:

| Hook                            | Purpose                                          |
| ------------------------------- | ------------------------------------------------ |
| `mirrors-clang-format`          | formatting, on changed files                     |
| `gersemi` or `cmake-format`     | `CMakeLists.txt` and `*.cmake` formatting        |
| `hadolint`                      | the Dockerfiles in `docker/`                     |
| `actionlint`                    | the workflows in `.github/workflows/`            |
| `shellcheck`                    | the entrypoints and helper scripts               |
| `codespell`                     | typos, including in comments and documentation   |

The last four exist because this project carries far more non-C++ configuration than the Python
one did -- seven workflows, five Dockerfiles and a set of shell entrypoints -- and those are
exactly the files where a mistake is not caught until CI runs.

### Documenting every function and every argument

This is enforceable, but only with the right Doxygen settings, and there is a trap:

```
EXTRACT_ALL           = NO                    # YES SILENTLY DISABLES the check below
WARN_IF_UNDOCUMENTED  = YES                   # "every function/class has documentation"
WARN_NO_PARAMDOC      = YES                   # "every argument is documented"
WARN_AS_ERROR         = FAIL_ON_WARNINGS      # report everything, then fail
```

`EXTRACT_ALL = YES` is the common default and it **suppresses undocumented-entity warnings**,
because it tells Doxygen to document everything whether or not the author wrote anything. With it
set, `WARN_IF_UNDOCUMENTED` has no effect and the check appears to pass while enforcing nothing.
`FAIL_ON_WARNINGS` is preferred over plain `YES` because it completes the run before failing, so
one push reports every missing comment rather than only the first.

A complementary check runs in CI, on the compiler rather than on Doxygen: **`-Wdocumentation` and
`-Wdocumentation-pedantic`** (Clang) verify that a doc comment *agrees with the code* -- a
`\param` naming an argument that does not exist, a documented return on a `void` function, a
parameter renamed without updating its comment. Doxygen catches documentation that is **missing**;
`-Wdocumentation` catches documentation that is **wrong**. The second failure mode is the one that
appears later in a project's life, when signatures change and comments do not.

### Naming conventions

clang-tidy's `readability-identifier-naming` covers the "variable cases" requirement, configured
per entity kind in `.clang-tidy`, and it can auto-fix.

There is a predictable conflict to plan for. The Python configuration already disables `N802`,
`N803` and `N806` because the algorithm is written in mathematical notation -- `R`, `L`, `K`, `P`,
`S`, `M`, `m`, `tau`, `nol`. The C++ port inherits those names, and a strict
`readability-identifier-naming` will reject most of them. Rather than weakening the rule
everywhere or annotating every occurrence with `NOLINT`, the resolution is to **confine the
mathematical names to the layer that implements the mathematics** -- the backends and the
algorithm classes -- and require descriptive names everywhere else, which is where an unfamiliar
reader will be. The exception list belongs in `.clang-tidy` with a comment pointing at this
paragraph.

### The clang-format configuration

`ALT-CPP` already has a `.clang-format`: Google-based, `IndentWidth: 4`, `ColumnLimit: 80`,
`PointerAlignment: Left`. That is a reasonable house style and is worth carrying over. Two
changes:

1. **Commit a minimal delta, not a `--dump-config` output.** The existing file is a full dump of
   every option for one clang-format version. New releases add options and occasionally change
   defaults, so a full dump silently pins the project to the assumptions of the version that
   produced it and produces large, meaningless diffs on every upgrade. `BasedOnStyle: Google` plus
   the five or six genuine overrides expresses the same intent and survives upgrades.
2. **Set `DerivePointerAlignment: false`.** The existing file sets `PointerAlignment: Left` and
   then leaves Google's `DerivePointerAlignment: true` in place, which tells clang-format to infer
   the alignment *per file* from what is already there -- so the explicit setting is ignored and
   `int* p` and `int *p` can both persist in different files.

### Keeping the hook and the image in agreement

clang-format output differs between major versions, so a developer running a different version
from CI will fight an endless reformatting loop. The version is pinned in **one** place and
consumed by both: the `rev` of `mirrors-clang-format` and the `clang-format` installed in
`dev-cpu` must match, and the simplest way to guarantee that is to **run `pre-commit` inside the
development container**, where they are identical by construction.

### Version consistency: not a check

The original plan listed "check that the version in the argparse and the documentation is
consistent with the latest git tag" as a pre-commit item. It is deleted here, because the version
is **derived** rather than duplicated -- see
[Version consistency](#version-consistency) under CI/CD. There is nothing to check when there is
only one source.

## Github CI/CD

### Workflows

| Workflow           | Trigger                                     | Does                                                    |
| ------------------ | ------------------------------------------- | ------------------------------------------------------- |
| `pre-commit.yml`   | pull request, push to `main`                | the same hooks as the local pre-commit                   |
| `dev-images.yml`   | push/PR touching `docker/**`                | build dev images if the content hash is absent; push     |
| `build-test.yml`   | pull request, push to `main`                | the preset matrix: configure, build, `ctest`             |
| `nightly.yml`      | schedule                                    | sanitizers, GPU build, Trivy, benchmarks                 |
| `release.yml`      | push of git tag `v*`                        | rebuild all, version tags, Trivy gating, GitHub release  |
| `docs.yml`         | push to `main`, git tag                     | Doxygen to GitHub Pages                                  |
| `cleanup.yml`      | schedule, weekly                            | prune the registry                                       |

`dev-images.yml` and `build-test.yml` form a dependency chain: the first outputs the
content-addressed dev image tag, the second consumes it as a `--build-arg BASE_IMAGE`, and the
runtime images are assembled from the compiled artefacts. This is the same layering described
under [Build structure](#build-structure), expressed as a job DAG.

### What runs when

The constraint is that a pull request must stay fast enough to be useful, while a merge to `main`
can afford breadth.

**Per pull request (target: under ten minutes)**

- `cpu-omp-release`: build and full `ctest`. This is the primary configuration.
- `cpu-serial-debug` with ASan and UBSan: build and full `ctest`. Cheap, and it catches undefined
  behaviour that the release build hides.
- `pre-commit`.

**Per merge to `main`**

- All CPU presets, release and debug.
- Coverage, from `coverage`, uploaded as a badge in the manner of the Python repository.
- **`cpu-omp-mpi-release` with `mpirun -n 4 --oversubscribe`.** A hosted runner has four vCPUs, so
  four ranks oversubscribe, but oversubscription affects speed and not correctness: the
  decomposition, the allgather and the collective HDF5 writes are all genuinely exercised. The
  MPI implementation can therefore be *finished and verified* in CI with no cluster; only the
  scaling numbers need real hardware.
- The cross-language round-trip against the committed Python fixtures.

**Nightly**

- Full sanitizer sweep, including TSan on the OpenMP build.
- The synthetic-dataset benchmark from development stage 4, with results retained so regressions
  are visible over time.
- Trivy over all images.
- The GPU build, see below.

### The GPU job builds, and still runs the tests

GitHub-hosted runners have no AMD GPU, so a HIP job can normally only prove that the code
compiles. Here it can do better, because **the backend is chosen at run time**: the binary built
by `gpu-hip-release` contains both backends, so on a GPU-less runner

```
altx-hip --device cpu
```

runs the entire test suite. The GPU job therefore validates that the HIP translation unit
compiles and links, *and* that the rest of the program is unbroken, on hardware that has no GPU.
Only the correctness of the device kernels themselves has to wait. This is a concrete payoff from
choosing a runtime seam over compile-time dispatch.

The GPU job is nightly, not per-PR: the ROCm toolchain image is several gigabytes, and pulling it
would dominate pull request latency.

### Runner constraints

Hosted `ubuntu-latest` runners provide four vCPUs, 16 GB of RAM and roughly 14 GB of free disk.
The disk is the binding constraint once ROCm is involved, so the GPU jobs need a disk-reclaim step
before the pull. Registry-backed build caching, as `docker-builder` already uses
(`type=registry,mode=max`), applies here unchanged and matters more, since a C++ toolchain layer
is far more expensive to rebuild than a `pip install`.

### Build documentation with Doxygen
The HTML documentation of the code should be build automatically with Doxygen. The docker images should be scanned for vulnerabilities.

Doxygen runs on every push to `main` and publishes to GitHub Pages. Warnings are errors, which is
what makes the "every function and every argument is documented" rule of the
[pre-commit section](#pre-commit) enforceable rather than aspirational. Note the
`EXTRACT_ALL = NO` requirement documented there: with `EXTRACT_ALL = YES` the check silently
enforces nothing.

Prose documentation lives in `docs/` as Markdown carrying a Doxygen `@page` command, so it appears
under "Related Pages" in the same HTML output as the API reference rather than as a second,
separate site. `docs/DevelopmentEnvironment.md` (`@page development_environment`) is the first of
these: the image matrix, the ways of running the containers, and the editor tooling. The Doxyfile
must therefore list `docs/` in `INPUT` alongside `src/`, and set
`USE_MDFILE_AS_MAINPAGE = docs/mainpage.md`.

`README.md` deliberately carries only the one command that starts an interactive session, and
links onwards. It is the file people skim; everything that would compete with that command for
attention belongs on the page above.

Vulnerability scanning follows `docker-builder`: Trivy with `scanners: vuln`, SARIF uploaded to
the GitHub Security tab under a per-image category, `ignore-unfixed: true` and a `.trivyignore`.
The difference is the gating, described under
[Vulnerability scanning](#vulnerability-scanning): runtime images fail on HIGH or CRITICAL,
development images are report-only.

### Run tests
The tests should be run automatically and code coverage should be calculated.

Test binaries are registered with CTest and labelled, so that a job can select a subset:
`unit`, `integration`, `mpi`, `gpu`, `slow`. The pull request jobs run everything except `slow`
and `gpu`.

### Version consistency

The plan asks for a check that the version in the argument parser and the documentation matches
the latest git tag. A check is the wrong mechanism: **derive the version instead**, with
`git describe --tags` feeding `Version.hpp.in` through `configure_file`, and have the argument
parser and the Doxygen configuration both read that single value. Then the versions cannot
disagree, and no CI job is needed to confirm it. The one thing still worth verifying is that a
release build was made from a clean, tagged commit, which `git describe --dirty --exact-match`
answers in one line in `release.yml`.

## High performance computing libraries

### HDF5
The inputs and outputs of the code should be saved to .csv and to .h5 files. For the .h5 files, it should be possible to read and write in parallel from and to the files if the filesystem is suitable.

In the code there should be a wrapper for the low-level C API.

The program parameters (ang full git hash), optionally the law-vectors, optionally the multiplied matrices, and the extracted features should be stored.

All of this goes into **one** file with optional groups, specified normatively under
[File formats](#file-formats). That schema is a bidirectional contract with the Python
implementation and must not be changed without a `format_version` bump.

### OpenMP
The serial and the OpenMP code should be the same but sometimes the OpenMP is enabled and sometimes disabled.

#### One thing breaks "the same source"

`#pragma omp` directives vanish when the compiler is invoked without `-fopenmp`, so the
directives themselves need no guarding. The **runtime API** does not: `omp_get_thread_num()` and
`omp_get_max_threads()` become undefined symbols, and guarding each call site with `#ifdef
_OPENMP` would scatter conditionals through the algorithm -- exactly what
[the two-axis design](#two-orthogonal-axes-not-three-versions) forbids.

The fix is a shim, `src/core/Omp.hpp`, exposing `altx::omp::thread_num()` and `max_threads()`
which return `0` and `1` when `_OPENMP` is undefined. **One `#ifdef`, in one file**, and the
algorithm never mentions OpenMP outside a pragma.

#### Where the parallelism goes

**Training** parallelises over a flattened `(instance, channel, window)` index space. It has to be
flattened rather than `collapse(3)`, because instances have different `train_length` values and
the loop nest is therefore not rectangular. That flat index space is the same one
[MPI splits](#parallel-decomposition), so a single decomposition serves both axes -- which is what
makes MPI+OpenMP a combination rather than a third implementation.

**Transforming** parallelises over instances in `transform_set`. For a single instance there are
no instances to spread, so the parallelism moves inward to the row blocks of
[the projection loop](#the-projection-is-a-thin-k-gemm-and-m-may-not-fit). These are two separate
parallel regions selected by which level is non-trivial; they are never nested.

#### "The same code" should also mean the same numbers

The requirement is worth reading strictly: serial and OpenMP builds should produce **bitwise
identical output**, not merely similar output. That is achievable here, and it converts the
serial-versus-OpenMP comparison from a tolerance check into an exact one, where any difference at
all is a bug rather than noise. Two things are needed.

**Compact the laws with a prefix sum, not an atomic counter.** Failed eigendecompositions leave
gaps, and the surviving laws must be packed down. Appending through an atomic index is the obvious
implementation and it is wrong: the resulting law order depends on thread scheduling, so `/laws`
would differ between runs and between thread counts. A prefix sum over the validity flags assigns
each surviving law the position it would have had serially, at negligible cost. This is what makes
the law-level comparison of
[canonical sign](#canonical-sign) meaningful across thread counts.

**Reduce in a fixed order.** `reduction(+:sum)` over the row axis gives a result that depends on
the number of threads, because floating-point addition is not associative. Instead, each block
writes its partial sum to a slot indexed by block number, and those partials are summed
sequentially afterwards. There are only `nol_tilde / B` of them, so the cost is nothing and the
result no longer depends on the thread count.

#### Thread-safety rules

- **All I/O happens outside parallel regions.** The HDF5 C library is not thread-safe unless
  specially built, and even then it serialises on a global lock. One thread does the writing.
- **Per-thread LAPACK workspace.** The `SymEigenSolver` of the
  [reference path](#two-implementations-one-interface-one-differential-test) holds a workspace, so
  each thread needs its own instance. The Jacobi kernel is stateless and needs nothing.
- **BLAS stays single-threaded** beneath the OpenMP loop, as described under
  [Threading](#threading-parallelism-at-one-level-only).

#### Scheduling and false sharing

Work per window is nearly constant -- same `l`, similar Jacobi iteration counts -- so
`schedule(static)` is the starting point, to be confirmed in development stage 4.

Static scheduling also matters for a reason beyond load balance. With `P` stored as
`(m, l, n_laws)`, consecutive laws are adjacent in memory, so threads writing neighbouring law
indices would share cache lines. Static scheduling gives each thread a contiguous run and confines
false sharing to the two boundaries per thread; `schedule(dynamic, 1)` would be pathological here.

#### What OpenMP is deliberately not used for

**GPU offload.** OpenMP 5 target offload is a genuine alternative to HIP, and it is rejected: the
Jacobi kernel and the radix select both want explicit control over thread-per-matrix mapping and
shared memory, and the project already commits to HIP plus [HOP](#nvidia-support-via-hop) for
portability. OpenMP here is a CPU-only construct. This is recorded so the choice is not
re-litigated later.

The baseline is OpenMP 4.5, which every compiler in the images supports comfortably.

#### Testing

The test suite runs at several thread counts and asserts **bitwise identical** results, which the
determinism rules above make a legitimate assertion rather than an aspiration.

ThreadSanitizer needs care: GCC's `libgomp` is not instrumented, so TSan reports a flood of false
positives on any OpenMP program built with it. The nightly TSan job must therefore use Clang's
`libomp`, or Archer, the OpenMP-aware TSan tool. Running TSan against `libgomp` and triaging the
output is wasted effort.

For benchmarking in stage 4, `OMP_PROC_BIND=close` and `OMP_PLACES=cores` are set by the benchmark
harness -- not hardcoded in the program -- so that measurements are stable and comparable.

### MPI
The load balancing might be difficult, if the dataset is imbalanced. There should be communicator for each unique class in the data, but then the load balancing will be difficult. The data can be divided easily between the ranks, but then the feature extraction will be difficult.

### HIP
It should be possible to run the code on NVIDIA and on AMD GPUs, so it will be written in HIP and
ported to CUDA with [HOP](https://github.com/cschpc/hop) if needed. See
[NVIDIA support via HOP](#nvidia-support-via-hop) for the consequences, the most important of
which is that the GPU backend must restrict itself to the runtime API and a batched GEMM. The
eigenproblems are solved by a hand-written Jacobi kernel, one thread per matrix, which is both
faster for `l` in the range 3 to 8 and free of the SOLVER dependency that HOP does not cover.

### LAPACK, BLAS
Wrapper classes will be written around the low-level APIs.

The code has exactly two linear-algebra needs, and they pull in opposite directions:

| Use            | Shape                                                        | Regime               |
| -------------- | ------------------------------------------------------------ | -------------------- |
| Training       | symmetric eigenproblem, `l x l`, `l` in 3..8, millions of them | call-overhead bound  |
| Transform      | `(nol_tilde x l) * (l x n_laws)` per channel                  | memory-bandwidth bound |

#### The eigenproblem: LAPACK is the wrong tool at this size

Python calls `torch.linalg.eigh`, which reaches LAPACK `?syevd`. Translating that literally would
be a mistake, because at `l = 4` the cost of a LAPACK call is dominated by the call itself:
workspace queries, argument validation, and `ILAENV` blocking lookups, all repeated for every one
of hundreds of thousands of windows. The matrix is too small for any of the blocked algorithms to
reach their advantage.

`?syevr` looks attractive because it can compute a subset, but it cannot help here: it selects
eigenvalues by **index in ascending order** or by value range, whereas the algorithm needs the
smallest eigenvalue *in absolute value*, which may sit anywhere in the spectrum. Getting it would
take one call for all eigenvalues and a second for the chosen vector -- two calls where the
problem wanted none.

The alternative is a **cyclic Jacobi eigenvalue iteration**, hand-written: no workspace, no
allocation, register-resident for `l <= 8`, branch-light, quadratically convergent, and with
better relative accuracy than QR on small or graded matrices. The same kernel then serves both
backends -- scalar or SIMD on the CPU, one thread per matrix on the GPU -- which is the shape
already required by [HOP's missing SOLVER coverage](#nvidia-support-via-hop).

That convergence is worth stating plainly: the hand-written eigensolver was introduced earlier as
a *GPU* necessity, but it is independently the right *CPU* choice, for an unrelated reason. One
kernel satisfies both, and the cross-backend diff test compares it against itself.

#### Two implementations, one interface, one differential test

The claim above is a prediction, not a measurement, so both paths are built behind the `LinAlg`
interface:

- **LAPACK `?syevd`** -- the reference implementation. It is what Python uses, so it doubles as the
  correctness oracle for the port.
- **Jacobi** -- the fast path.

A unit test runs both over random symmetric matrices and asserts agreement, and development
stage 4 decides which is the default. Keeping the reference path permanently is cheap and means a
suspicious result can always be checked against LAPACK.

#### Canonical sign

Eigenvectors are defined only up to sign, and LAPACK and Jacobi will disagree. The features are
unaffected, since the projection result is squared, but the *laws* are not comparable across
implementations without a convention.

Fixing one costs a single pass over `l` elements: **make the component of largest magnitude
positive** (more stable than using the first component, which may be near zero). With this, the
`/laws` group becomes directly comparable between the CPU and GPU backends and between C++ and
Python, upgrading the round-trip test from "compare features" to "compare laws as well". Note that
this removes only the *sign* ambiguity -- law **ordering** still depends on the loop order, which
is deterministic in serial but permuted under MPI, so law-level comparison stays a serial-only
check.

#### Near-degenerate eigenvalues

When the two smallest absolute eigenvalues are close, the choice between them is ill-conditioned:
a perturbation far below the tolerance flips the selection, and the two eigenvectors are
completely different. This is not a bug to fix but a property of the problem, and it is the most
likely cause of a mysterious round-trip test failure.

The implementation therefore **counts the windows whose two smallest absolute eigenvalues are
within a relative gap threshold** and reports the number, and the committed fixtures are chosen to
have no such cases. Without this, an unreproducible comparison failure would be very hard to
diagnose.

#### Row-major, column-major, and one simplification

`ALT-CPP`'s TODO list carried "rethink column and row major data storage" as an open problem. Much
of it dissolves on inspection:

- LAPACK is column-major, but the input `S` is **symmetric**, so its row-major and column-major
  representations are byte-identical. It can be passed straight through with no transpose.
- The eigenvector output is not symmetric, but the chosen eigenvector is a *column* of `V`, which
  in column-major storage is contiguous -- so extracting it is a straight copy of `l` values.

For BLAS, the wrapper uses CBLAS with `CblasRowMajor` where available and the
`C^T = B^T A^T` argument-swap otherwise. This is precisely the kind of thing a wrapper exists to
settle once, so that no call site ever reasons about it again.

#### The projection is a thin-k GEMM, and `M` may not fit

The inner dimension is `l`, between 3 and 8, while the other two are large. Arithmetic intensity
is therefore low and the operation is **bandwidth-bound, not compute-bound**, which means the
choice of BLAS vendor will matter far less here than one might expect.

The more serious consequence is size. For the running example of roughly 500k laws over two
classes and a test instance yielding `nol_tilde` of a few hundred, `M` for one class is about
`500 x 250000` values -- a gigabyte, for a single instance. Python materialises it because its
problems have been small enough. That will not scale.

**The fix is to nest the loops correctly, and then there is no memory problem at all.** The
quantile reduces along the law axis *independently for each row*: row `i` needs every value of row
`i` and nothing from any other row. So the rows are independent, and the blocking goes over the
**row** axis, not the law axis:

```
for each class c, channel j:
    for each block of B rows:
        M_blk = data[rows] * P[:, laws of class c]   # B x n_laws_c, one GEMM
        square in place
        for each row: exact quantile, accumulate the statistic over rows
```

Peak memory is `B * n_laws_c * sizeof(Real)`, so `B` is a tuning knob for cache behaviour and GEMM
efficiency rather than a feasibility constraint. In the example a single row is 2 MB and `B = 64`
is 128 MB. The GEMM re-reads `P` once per block, but `P` is only about 16 MB: re-reading the
*small* operand to avoid materialising the *large* result is the right trade.

The per-row quantile uses **`std::nth_element`, not a sort**. Torch's linear interpolation needs
the order statistics at `floor((n-1)q)` and the next one, which is one `nth_element` plus a
`min_element` over the upper partition -- `O(n)` against `O(n log n)`. For several quantiles, sort
them ascending and partition left to right, each call restricted to the sub-range left by the
previous one. **The interpolation convention must match torch exactly**, or the round-trip test
produces small unexplained disagreements.

#### Selection when a row is not in one place

Row-blocking fails in exactly one situation: when a single row's values are **distributed across
ranks**, which happens only if the laws ever stop being replicated
(see [Parallel decomposition](#parallel-decomposition)). Then no rank can call `nth_element` on a
whole row, and selection has to be done without gathering.

The standard answer is **two-pass histogram selection**. To find the value at rank `r` among `N`
values: partition the value range into `B` buckets and have every rank histogram its local share,
then `MPI_Allreduce` the `B` counters -- reducing the whole dataset to `B` numbers. The cumulative
counts identify the bucket containing rank `r`, and how many values `C` lie strictly below it. A
second pass keeps only values inside that bucket, roughly `N/B` of them, which are then either
gathered for an exact `nth_element` of the `(r - C)`-th smallest, or recursed on. With `B = 1024`
this converges in two or three passes, and the data is never materialised -- only counters are.

The variant worth implementing is **radix select on the bit patterns**, for two reasons specific
to this algorithm:

- The values are `M`-squared and therefore **non-negative**, and for non-negative IEEE-754 floats
  the bit pattern read as an unsigned integer orders identically to the float. So the histogram
  can be taken directly on bit patterns: no min/max pre-pass, no floating-point bucket boundaries,
  and the result is **exact** rather than interpolated.
- The [no-NaN policy](#numerical-policy-nan-is-not-a-legal-value) is what makes this safe, since
  NaN is the one bit pattern that breaks the monotonic mapping.

This is not purely a contingency. `nth_element` suits a CPU and suits a GPU badly, whereas radix
select is the standard GPU selection primitive, so the HIP backend is likely to want it
regardless. CPU uses `nth_element`, GPU uses radix select, both exact, and the cross-backend diff
stays meaningful.

#### Threading: parallelism at one level only

BLAS libraries thread internally. Calling a threaded BLAS from inside an OpenMP parallel region
oversubscribes the machine, and for the tiny GEMMs here it is pure loss. The rule is that
**OpenMP parallelises over windows and instances, and BLAS runs single-threaded** beneath it,
enforced in code rather than left to environment variables.

One specific landmine: OpenBLAS ships in pthread, OpenMP and serial builds, and the pthread build
is known to deadlock or degrade badly when called from OpenMP regions. Ubuntu exposes these as
`libopenblas-{pthread,openmp,serial}-dev`. The images must select deliberately rather than take
whatever the alternatives system points at.

#### Shape of the wrapper

The plan says "wrapper classes". For BLAS that is the wrong shape: the operations are stateless,
so a class with no members is a namespace with extra syntax. The wrapper is a namespace of free
function templates with `float`/`double` specialisations selecting `s`/`d` routines -- the one
genuinely templated component identified under [Scalar type](#scalar-type).

A class is justified in exactly one place: the LAPACK reference path needs a workspace, and
allocating it per call would reintroduce the overhead the design is trying to avoid. That becomes
a small `SymEigenSolver` object holding a reusable per-thread workspace. The Jacobi path needs no
workspace at all.


### Other development choices

**C++ standard: C++20.** Two features earn it directly rather than as a matter of taste:
`std::span`, which is exactly the non-owning view that `Tensor` needs to hand out, and
`std::bit_cast` in `<bit>`, which is exactly what the
[radix select](#selection-when-a-row-is-not-in-one-place) needs to reinterpret floats as unsigned
integers without undefined behaviour. GCC 13 on Ubuntu 24.04 covers this comfortably. The one
risk is `hipcc` lagging on C++20; if it does, the `.hip.cpp` translation unit drops to C++17
alone, which is harmless because it sits behind the backend interface and shares no templates with
the host code.

**Exceptions, but not everywhere.** They are used for input validation, file format errors and
configuration mistakes -- all of which happen once, outside any loop. They are used **never** in
the compute kernels, which is not a style preference: HIP device code cannot use them at all, so
the kernels must be exception-free by construction if the CPU and GPU paths are to share
structure.

> One consequence that is easy to miss: **an exception escaping on a single MPI rank hangs the
> whole job**, because the other ranks wait forever in a collective the aborting rank will never
> reach. Every rank's entry point therefore wraps its work in a `try`/`catch` that reports and
> calls `MPI_Abort`. Getting this wrong produces a job that hangs instead of failing, which is far
> more expensive to diagnose.

**Logging** is a small header-only, level-based, rank-aware facility written in-project rather
than a dependency such as spdlog -- consistent with the decision to avoid Boost. Only rank 0
prints by default, output happens on one thread, and `--verbose` raises the level.

**Namespace** `altx`, with sub-namespaces mirroring the directories (`altx::io`,
`altx::backend`, `altx::dist`).

**Random numbers** appear only in the benchmark harness and the synthetic dataset generator, never
in the algorithm. They use `std::mt19937_64` with an explicit seed that is recorded in
`/parameters`, so a benchmark run can be reproduced exactly.

## Argument parser
I do not want to use Boost in the project, so the argument parser of the code will be the: [github/p-ranav/argparse](https://github.com/p-ranav/argparse).

It is header-only and MIT licensed, it supports the subcommands that
[the CLI](#command-line-interface) needs, and it is vendored the same way as HOP and GoogleTest
(see [Dependencies](#dependencies)).

### The parser fills `Parameters`; nothing else reads argv

`argparse` is confined to `apps/altx_main.cpp`. It parses, validates and populates a single
`Parameters` object, and everything below receives that object. No library code includes the
parser header or touches `argv`. Three things follow from this, all of which are the reason for
the rule:

1. **Tests construct `Parameters` directly**, so every configuration is reachable from a unit test
   without building a command line.
2. **`/parameters` in the output file is a serialisation of that one object**, so the provenance
   record cannot drift from what the program actually did.
3. A future non-CLI entry point costs nothing.

### Validation happens once, in one place

The Python implementation validates `R`, `L` and `K` inside `__init__`, mixing type coercion,
broadcasting of scalars against lists, and mathematical constraints. In C++ this splits cleanly:

- **`argparse`** handles syntax -- the option exists, it is an integer, it appears the right number
  of times.
- **`Parameters::validate()`** handles semantics -- `R`, `L`, `K` broadcast to a common length;
  every value positive; `(r-1) % (2l-2) == 0`; `r` no larger than the shortest instance.

The semantic rules live with the data, not with the parser, because they must also run when
`Parameters` is reconstructed from the `/parameters` group of an existing file rather than from a
command line. Validation that only exists in the parser would be skipped on exactly the path where
a mismatched file is most likely.

### Extraction methods on the command line

The Python API takes `[["mean", 0.05], ["mean_all"]]`, where a missing quantile defaults to `0.05`
and `mean_all` takes none. The CLI spelling is `--extract mean:0.05 --extract mean_all`, repeated
per method, with the same defaulting rule.

> The Python implementation **mutates the caller's list in place** while applying that default, so
> reusing one list across calls changes its meaning. The C++ parser produces a new value and never
> writes back. This is a deliberate divergence and it cannot affect the round-trip comparison,
> since each fixture run parses its methods once.

## Storing tensors
Since for MPI and HIP the values has to be stored in a way that a raw pointer should be available for the data. Also the row-major order of the data should be consistent.

The multi-dimensional arrays will be stored as a Tensor object which handles the manual indexing.
It is *not* a wrapper around `std::vector`: device memory cannot be allocated into one sensibly,
and host memory should be aligned for BLAS and SIMD. Instead the storage is a small owning class
parameterised on an allocator policy, `Tensor<Real, HostAlloc>` (64-byte aligned) and
`Tensor<Real, HipAlloc>` (`hipMalloc`). It still exposes `data()` as a raw pointer, which is all
that MPI and HIP require.

The layout principle throughout the code is **channel index outermost**, because every stage of
the algorithm is independent per channel and this is the only layout that lets the projection go
straight into BLAS. See rule 2 of [the ALTX file specification](#the-altx-file-normative)
for the derivation.

## Scalar type
The scalar type is a **typedef selected at configure time**, `ALTX_SCALAR = double` (default) or
`float`, exposed as `using Real = ...`. Since the deliverable is a CLI tool, exactly one
instantiation exists per binary, so templating the algorithm would buy nothing while costing
compile time. A `float` build for the GPU is a different binary and a different image anyway.

The one component that *is* templated is the BLAS and LAPACK wrapper (`src/backend/cpu/LinAlg.hpp`),
because `ssyevr`/`dsyevr` and `sgemm`/`dgemm` need compile-time selection and because both paths
should be unit tested in a single test binary regardless of `ALTX_SCALAR`. Everything above that
wrapper says `Real`.

Consequence for the tests: the comparison tolerance is a function of the build configuration, so
the fixtures take a tolerance parameter instead of a hardcoded epsilon. The proposed values are a
relative tolerance of `1e-10` for `double` and `1e-4` for `float`.

## Numerical policy: NaN is not a legal value

The Python implementation uses NaN as ordinary control flow: as an "unfilled" sentinel in
`P_classes`, and as a signal that a statistic could not be computed, in which case it substitutes
zeros. The C++ implementation **forbids NaN entirely**. It is rejected at the boundary and
prevented at every point where it could be created, so no `isnan` test is needed in the pipeline.

This requires a decision at each of the places where the Python version currently produces one:

| Source of NaN in Python                                     | C++ behaviour                                      |
| ----------------------------------------------------------- | -------------------------------------------------- |
| Non-finite values in the input data                          | **Rejected on load**, see below                     |
| `P_classes` initialised to NaN, compacted later              | Explicit validity flags, compacted; no sentinel     |
| Eigendecomposition failed (`_LinAlgError`)                   | The law is not stored; failures counted and reported|
| `excess_kurtosis`: `fourth_moment / variance**2` with zero variance | Tested **before** dividing, result defined as 0 |
| `var` over a single sample (`n - 1 == 0`)                    | Tested before dividing, result defined as 0         |
| `nth_moment` overflow for large `n`                          | Detected as non-finite and reported as an error     |

**Input validation is the most valuable part of this.** Real time series datasets contain missing
values, and the Python implementation propagates them silently: one NaN in one channel yields a
feature vector of NaNs, which a downstream classifier will happily consume. The C++ reader scans
for non-finite values on load and fails with the instance, channel and time index. A
`--on-nonfinite=error|drop-instance` switch can relax this later; the default is `error`.

The invariant is kept honest by an `assert_all_finite()` check after each stage, active in debug
builds and compiled out in release. It is a debugging aid, not the enforcement -- the enforcement
is that no operation can create a NaN in the first place.

### Deliberate divergences from Python

Two of these are behaviour changes, not just refactorings, and the cross-language round-trip test
has to account for them:

1. **Input containing NaN**: Python returns NaN features, C++ refuses to run. There is no
   agreeing answer, so the fixtures must be NaN-free -- which they are, being synthetic.
2. **Zero variance**: Python's `excess_kurtosis` and `var` check `isnan().any()` and then return
   `zeros_like(...)`, zeroing **the entire tensor** when any single element is NaN. C++ handles
   each element independently, so where Python zeroes a whole vector, C++ zeroes only the
   degenerate entries. This is arguably a bug in the Python implementation; if a fixture ever
   triggers it the two will disagree, and the right fix is to correct the Python side rather than
   to reproduce the behaviour.

The fixtures should include a case that *approaches* the degenerate condition -- a constant time
series, which drives the variance to zero -- so that the divergence is exercised deliberately
rather than discovered later.

## CMake
For compiling the code CMake will be used.

`cmake_minimum_required(VERSION 3.25)`. That is the floor for preset schema 6 and for mature
first-class HIP language support, and Ubuntu 24.04 ships 3.28, so it costs nothing.

### Everything is target-scoped

`ALT-CPP` used directory-scoped commands -- `link_libraries(${BLAS_LIBRARIES})`,
`include_directories(...)`, `add_definitions(-DINTEL_MKL)` -- which apply to every target created
afterwards, including the tests and any future tool. The replacement rule is that **nothing is
directory-scoped**: every include directory, definition and link is attached to a target with
`target_*` and an explicit `PUBLIC`/`PRIVATE`/`INTERFACE` keyword.

Cross-cutting concerns become `INTERFACE` targets that other targets link:

| Target             | Carries                                                     |
| ------------------ | ----------------------------------------------------------- |
| `altx_warnings`    | the warning set, and `-Werror` when `ALTX_WERROR` is on      |
| `altx_sanitizers`  | the ASan/UBSan/TSan flags, empty when disabled               |
| `altx_coverage`    | the gcov flags, empty when disabled                          |
| `altx_hop`         | HOP's include directories, `HOP_TARGET_*`, and the `-x` flag |

### Options

```
ALTX_ENABLE_OPENMP    ON            ALTX_SCALAR         double | float
ALTX_ENABLE_MPI       OFF           ALTX_BLAS_VENDOR    OpenBLAS | MKL | AOCL
ALTX_ENABLE_HIP       OFF           ALTX_SANITIZERS     "" | address,undefined | thread
ALTX_ENABLE_TESTS     ON            ALTX_WERROR         OFF (on in CI)
ALTX_ENABLE_COVERAGE  OFF           ALTX_NATIVE_ARCH    OFF
```

The options are orthogonal by construction, and an unsupported combination must **fail at
configure time with a readable message** rather than produce a subtly wrong build --
`ALTX_ENABLE_HIP=ON` without a HIP compiler being the obvious case.

### One library, conditional sources

`ALT-CPP` declared `SOURCES` and `PARALLEL_SOURCES` as two identical lists and built `alt_lib` and
`alt_parallel_lib` from them. That is the duplication that
[Two orthogonal axes](#two-orthogonal-axes-not-three-versions) exists to prevent, and CMake is
where it either happens or does not.

There is **one** `altx_core` target. MPI and HIP add *files*, never copies:

```cmake
add_library(altx_core STATIC ${ALWAYS_COMPILED_SOURCES})
if(ALTX_ENABLE_MPI)
    target_sources(altx_core PRIVATE src/dist/MpiComm.cpp)
endif()
if(ALTX_ENABLE_HIP)
    target_sources(altx_core PRIVATE src/backend/hip/HipBackend.hip.cpp)
endif()
```

`SerialComm.cpp` and `CpuBackend.cpp` are always compiled. This is the two-axis design expressed
as a build file, and it is the single most important thing in this section.

### How one image gets two binaries

`runtime-cpu` ships `altx-serial` and `altx-omp`. These differ in `ALTX_ENABLE_OPENMP`, which is a
configure-time option, so they cannot come from one build directory. They come from **two preset
builds in the Docker builder stage**, whose outputs are both copied into the runtime image:

```
cmake --preset cpu-serial-release && cmake --build --preset cpu-serial-release
cmake --preset cpu-omp-release    && cmake --build --preset cpu-omp-release
```

This is why the preset table and the image table are one artifact: a preset is the unit of build,
an image is a set of preset outputs.

### Dependencies

`find_package` with imported targets throughout -- `BLAS::BLAS`, `LAPACK::LAPACK`,
`MPI::MPI_CXX`, `OpenMP::OpenMP_CXX`, `HDF5::HDF5`. BLAS vendor selection goes through
`BLA_VENDOR` rather than the hardcoded `/opt/intel/oneapi/...` paths and manual
`-Wl,--start-group` lists of `ALT-CPP`. HDF5 selects its flavour with
`set(HDF5_PREFER_PARALLEL ${ALTX_ENABLE_MPI})` before the `find_package`, using the C API as the
plan requires.

> The elaborate RPATH handling in `ALT-CPP` -- `CMAKE_BUILD_WITH_INSTALL_RPATH`, and explicitly
> removing `$ENV{CONDA_PREFIX}/lib` from the RPATH -- existed because a conda installation was
> polluting the library search path. Inside the container there is no conda, so **all of it can be
> deleted**. This is a concrete, immediate payoff from building in Docker.

`argparse`, HOP and GoogleTest come through `FetchContent`, pinned to **commit hashes rather than
tags**, since a tag can be moved. To keep CI and offline builds from depending on the network,
the development image pre-populates `FETCHCONTENT_BASE_DIR`, so a configure inside the image
resolves them locally.

### Three compiler-flag decisions

1. **`-march=native` must default to OFF.** `ALT-CPP` had it unconditionally in
   `CMAKE_CXX_FLAGS_RELEASE`. That was safe when the binary was built and run on one workstation.
   It is *not* safe now: an image built on a GitHub runner and run anywhere else will die with an
   illegal instruction on the first AVX-512 path the runner happened to support. It becomes
   `ALTX_NATIVE_ARCH`, off for images, on for local benchmarking in stage 4.
2. **`-ffast-math` is forbidden, and the reason must be written down.** Two independent reasons,
   both of which survive the [no-NaN policy](#numerical-policy-nan-is-not-a-legal-value):
   - `-ffinite-math-only` lets the compiler assume no NaN or infinity can occur, and therefore
     **deletes the validation that enforces the policy**: the input scan and every
     `assert_all_finite()` fold to constant false. Banning NaN as a value makes the checks that
     detect it *more* load-bearing, not less, because they are now the only thing standing between
     a malformed input and silently plausible wrong numbers. Overflow to infinity in the higher
     moments also remains possible regardless of the policy.
   - `-fassociative-math` reorders floating-point operations, so results become a function of the
     optimisation level and of whether a loop vectorised. That breaks both comparisons the test
     strategy is built on: the cross-language round trip against the Python fixtures and the
     cross-backend CPU/GPU diff.
3. **`-Werror` in CI only.** A permanently fatal warning is hostile mid-refactor, but a warning
   nobody must fix is decoration. `ALTX_WERROR` defaults off and the CI presets turn it on.

### The version header and provenance

The output file records the full git hash, so a **stale** hash is not cosmetic -- it mislabels
results. Generating `Version.hpp` with `configure_file` at configure time is therefore not
sufficient: every commit after the configure would silently write the wrong hash. It is generated
at **build** time by a custom target that runs `git describe --tags --dirty --always` and writes
the header only when the content changes, so it is both correct and does not force a rebuild on
every invocation.

`--dirty` matters: a build from a modified working tree gets a hash marked as such, so results
that cannot be reproduced from any commit are visibly labelled instead of appearing trustworthy.

### HIP language support

`enable_language(HIP)` is called only under `ALTX_ENABLE_HIP`, with `CMAKE_HIP_ARCHITECTURES` set
from a cache variable. Because the same `.hip.cpp` source is compiled either as HIP or, through
HOP, as CUDA, the language and flags for that file are applied by a helper function rather than
inline, keeping the choice in one place. See [NVIDIA support via HOP](#nvidia-support-via-hop).

### Testing and presets

`gtest_discover_tests` registers the tests with `LABELS` matching the CI selection: `unit`,
`integration`, `mpi`, `gpu`, `slow`. MPI tests are registered through `mpiexec` wrappers so they
are ordinary CTest entries.

`CMakePresets.json` is committed and holds the matrix from
[Presets map one-to-one onto images](#presets-map-one-to-one-onto-images), built by inheritance
from a common base rather than repeating cache variables. `CMakeUserPresets.json` is for personal
configurations and belongs in `.gitignore`. Every preset sets
`CMAKE_EXPORT_COMPILE_COMMANDS=ON`; a committed `.clangd` points at one build directory so that
clangd works without symlinking the compilation database into the source root.

### Install

Only the executables: `install(TARGETS altx-... RUNTIME DESTINATION bin)`. Because the deliverable
is a CLI tool, there is no header installation, no library installation and no export set --
`ALT-CPP`'s `install(DIRECTORY ... FILES_MATCHING PATTERN "*.hpp")` is deliberately not carried
over.

## Google Tests
For the testing Google Tests will be used. There should be both unit tests and integration tests. Experimenting with test driven development might also be employed.

### Cross-language round-trip test
Because the [ALTX file schema](#the-altx-file-normative) is a contract with the Python
implementation, the central integration test is a round trip:

1. Python trains on a fixed seed and writes an ALTX file containing `/laws`, `/data` and its own
   `/features`.
2. C++ reads that one file for both the laws and the data, transforms, and writes its own
   `/features`.
3. The two feature sets are compared within the tolerance of the build configuration.

Small fixtures (a seeded model and the expected features, a few hundred kilobytes) are committed
to the repository rather than regenerated in CI, so that the C++ test job needs no Python
interpreter and the comparison is deterministic. `scripts/gen_reference.py` regenerates them.

The comparison is primarily on **features**, which are always well defined: the algorithm squares
the projection result, so the sign ambiguity of eigenvectors cannot reach them.

Laws can additionally be compared, but only under two conditions established elsewhere in this
document: the [canonical sign](#canonical-sign) convention must be applied on both sides, and the
comparison must be serial, because law **ordering** is deterministic in serial but permuted under
MPI. Where both hold, the law-level comparison is much sharper than the feature-level one, since
it localises a discrepancy to a single window instead of to a whole feature vector.

A fixture that deliberately approaches the degenerate cases belongs in the set: a constant time
series, which drives the variance to zero and exercises the
[divergences from Python](#deliberate-divergences-from-python). Conversely, fixtures must contain
no [near-degenerate eigenvalues](#near-degenerate-eigenvalues), because there the choice of law is
genuinely ill-conditioned and a disagreement would indicate nothing.

### Cross-backend test
The same input is pushed through the CPU and the HIP backend and the outputs are diffed. This is
the reason the backend seam is a runtime choice rather than a compile-time one.

### Test taxonomy

Tests are labelled for CTest so that CI can select subsets
(see [Run tests](#run-tests)):

| Label         | Contents                                                                  |
| ------------- | ------------------------------------------------------------------------- |
| `unit`        | one component in isolation: `Tensor` indexing, `RLK` validation, the readers and writers, each extraction method |
| `integration` | end to end through the CLI, including the round trip above                 |
| `mpi`         | launched through `mpiexec`; run with `-n 4 --oversubscribe` in CI          |
| `gpu`         | requires a device; skipped on hosted runners, where `--device cpu` covers the rest |
| `slow`        | the sanitizer and large-input runs, nightly only                           |

Three differential tests carry disproportionate weight, because each one pins an
implementation against an independent oracle rather than against a hardcoded expectation:

1. **Jacobi against LAPACK** on random symmetric matrices
   ([two implementations](#two-implementations-one-interface-one-differential-test)).
2. **Serial against OpenMP**, asserting *bitwise* equality, which the determinism rules make a
   legitimate demand rather than an aspiration
   (see [the same numbers](#the-same-code-should-also-mean-the-same-numbers)).
3. **CPU against HIP**, as above.

Test-driven development fits the extraction methods and `Tensor` particularly well, since their
expected values can be written down independently of any implementation. It fits the backends
less well, where the oracle is another implementation rather than a known answer.

## Development stages
0. Planning the code structure and development process.
1. (current) Setting up development environment: creating first docker images with vim and VS Code where autocomplete is set-up.
2. Freezing the ALTX file schema and modifying the Python implementation to write it. This comes
   before the C++ algorithm, because it produces the fixtures that every later stage is tested
   against.
3. Creating the serial version of the code. **This is the first milestone**: serial and OpenMP
   working and matching the Python implementation.
4. Profiling the serial version. Creating another project with generated synthetic dataset and profiling with which the different versions can be compared.
5. Creating and profiling MPI version.
6. Creating and profiling HIP version.

What each stage needs in the way of hardware, given that there is no cluster yet:

| Stage | Testable now?                                                                        |
| ----- | ------------------------------------------------------------------------------------ |
| 1--4  | Yes, entirely on a workstation.                                                       |
| 5     | **Correctness yes, scaling no.** `mpirun -n 4` on one machine exercises the decomposition, the allgather and the collective HDF5 writes, so the logic can be finished and tested. Multi-node scaling numbers must wait for cluster access. |
| 6     | Needs an AMD GPU. Blocked on hardware.                                                |

Because stage 6 is hardware-blocked and stage 5 is only half-verifiable, the order of 5 and 6 is
not fixed; whichever hardware becomes available first should go first. The architecture makes this
cheap, since the backend axis and the distribution axis are independent.

## Stage 3 in detail: build order to a correct serial implementation

The order below is chosen on two principles:

1. **Never write code that cannot yet be run.** Every milestone ends with something executable and
   a test that can fail.
2. **Front-load the risk.** The numerically dangerous parts are written and validated *before*
   anything depends on them, and before the infrastructure that would make debugging them slower.

The second principle is why the algorithm comes **before** HDF5, which looks backwards at first
glance: the fixtures are `.h5` files, so surely the reader must come first? No -- and the reason is
worth stating, because it saves a great deal of time.

> **The Python docstrings already contain exact expected values.** `ExtractMethods.excess_kurtosis`
> of `[[[1.],[2.],[3.],[4.],[5.]]]` is documented as `-1.3`; `nth_moment` with `n=2` and `n=4` as
> `2.0` and `6.8`; `extract(ones(5,20,2), [["mean", 0.5]])` as `[[1., 1.]]`; and `Altx` on a seeded
> `torch.randn(6, 50)` yields `RLK == ((5,3,1),)`, `P.shape == (3, 276, 1)` and a documented
> three-row feature matrix. These are a ready-made test suite for the entire numerical core,
> usable **before a single line of HDF5 exists**. Transcribe them into GoogleTest fixtures.

### Milestone 0 -- the toolchain runs

| # | File | Proven by |
| - | ---- | --------- |
| 1 | `CMakeLists.txt` (minimal: project, C++20, options) | it configures |
| 2 | `cmake/CompilerWarnings.cmake`, `cmake/Dependencies.cmake` (GoogleTest via FetchContent) | -- |
| 3 | `CMakePresets.json` -- only `cpu-serial-debug` and `cpu-omp-release` for now | `cmake --preset` works |
| 4 | `tests/CMakeLists.txt`, `tests/unit/test_smoke.cpp` | `ctest` reports one passing test |

Add the remaining presets later, when there is something to build with them. Two are enough to
prove the option axes are orthogonal.

### Milestone 1 -- core value types

Everything here is pure, header-mostly and testable in isolation.

| # | File | Proven by |
| - | ---- | --------- |
| 5 | `src/core/Exceptions.hpp`, `src/core/Logger.hpp` | -- |
| 6 | `src/core/Tensor.hpp` | `test_tensor.cpp`: row-major indexing, the `(m, l, n_laws)` layout, alignment, move semantics |
| 7 | `src/core/RLK.hpp` | `test_rlk.cpp`: `(r-1) % (2l-2) == 0`, broadcasting of scalars against lists, every rejection the Python `__init__` performs |
| 8 | `cmake/GitVersion.cmake`, `src/core/Version.hpp.in` | the hash changes when a commit is made without reconfiguring |
| 9 | `src/core/Parameters.{hpp,cpp}` with `validate()` | `test_parameters.cpp` |
| 10 | `src/io/DataSet.hpp` -- the in-memory value type only, **no HDF5** | -- |
| 11 | `src/dist/Communicator.hpp`, `src/dist/SerialComm.hpp` | -- |

Items 10 and 11 are worth their placement. Separating `DataSet` (a value type) from `AltxFile`
(the HDF5 machinery) keeps the algorithm testable with hand-built data. And writing `SerialComm`
now, rather than when MPI arrives, means every later component is built against the communicator
interface from the start -- which is what makes the MPI version an addition rather than a rewrite.

### Milestone 2 -- the numerical heart, still no I/O

| # | File | Proven by |
| - | ---- | --------- |
| 12 | `src/backend/cpu/LinAlg.hpp` -- traits, `gemm`, `syev` for `float`/`double` | `test_linalg.cpp`: GEMM against hand-computed products, both scalar types |
| 13 | `src/backend/cpu/SymEigenSolver.{hpp,cpp}` -- **LAPACK path first**, plus canonical sign and the near-degeneracy counter | `test_eigen.cpp`: analytically known matrices; the canonical sign is stable under input sign flips |
| 14 | `src/backend/cpu/Jacobi.hpp` -- the hand-written solver | `test_eigen_differential.cpp`: Jacobi against LAPACK over random symmetric matrices |

**LAPACK before Jacobi**, deliberately. The first comparison against Python should differ in as few
respects as possible, and Python reaches LAPACK through `torch.linalg.eigh`. Introducing a
hand-written eigensolver at the same time as everything else means a mismatch has two candidate
causes. Once the pipeline agrees with Python using LAPACK, Jacobi is added and validated against a
working reference -- which is exactly the differential test the plan already calls for.

### Milestone 3 -- algorithm kernels, validated against the docstrings

| # | File | Proven by |
| - | ---- | --------- |
| 15 | `src/algo/Embed.hpp` -- windowing and the strided embedding | `test_embed.cpp` against hand-computed windows |
| 16 | `src/backend/Backend.hpp` -- the abstract seam | it compiles |
| 17 | `CpuBackend::extractLaws` | `test_extract_laws.cpp`: law counts, the prefix-sum compaction, `P.shape == (3, 276, 1)` from the docstring |
| 18 | `src/algo/FeatureExtractor` -- quantile plus `mean`, `var`, `excess_kurtosis`, `nth_moment`, `mean_all` | `test_extract_methods.cpp`, transcribed from the Python docstrings |
| 19 | `CpuBackend::project` -- the row-blocked GEMM | `test_project.cpp` |

At the end of this milestone the whole numerical core is verified and **nothing has been read from
disk**.

### Milestone 4 -- I/O

| # | File | Proven by |
| - | ---- | --------- |
| 20 | `src/io/H5Wrapper.hpp` -- RAII over `hid_t`, so no handle is leaked on an exception path | `test_h5wrapper.cpp` |
| 21 | `src/io/AltxFile` **read** -- optional groups, dtype conversion, the consistency checks | `test_altxfile_read.cpp` against a committed Python-written fixture |
| 22 | `src/io/AltxFile` **write** | `test_altxfile_roundtrip.cpp`: write, read back, compare |
| 23 | `src/io/CsvWriter` -- including the `_generate_header` column naming | `test_csv_writer.cpp` |
| 24 | `src/io/CsvReader`, `src/io/ArffReader` | only if a real dataset needs them; otherwise defer |

Read before write: the fixtures must be consumable before anything produced is worth checking. The
RAII wrapper genuinely comes first -- retrofitting it after the reader exists means revisiting
every error path.

### Milestone 5 -- assembly

| # | File | Proven by |
| - | ---- | --------- |
| 25 | `src/LawExtractor.{hpp,cpp}` | `test_pipeline.cpp`: in-process, fixture in, features out |
| 26 | `src/Transformer.{hpp,cpp}` | as above |

### Milestone 6 -- the CLI and the real exit criterion

| # | File | Proven by |
| - | ---- | --------- |
| 27 | `apps/altx_main.cpp` -- `train`, `transform`, `run` | `--help` for each subcommand |
| 28 | `tests/fixtures/` -- the committed `.h5` files from stage 2 | -- |
| 29 | `tests/integration/test_cli_roundtrip.cpp` | **the definition of done** |

**Stage 3 is complete when** `altx transform` reads a model trained by Python, transforms the same
data, and produces features matching Python's within tolerance -- and the serial and OpenMP builds
agree bitwise with each other.

### Milestone 7 -- hardening

Sanitizers clean on the full suite; the no-NaN validation paths exercised by deliberately malformed
inputs; error messages checked for naming the offending instance, channel and index;
`.pre-commit-config.yaml` and the CI workflows wired up.

### A tolerance trap to expect at milestone 6

The Python implementation works largely in **float32** -- `P` is allocated with the default dtype
and `multiply_only` casts to `torch.float32` -- while the C++ default is `double`. The reference
values therefore carry float32 rounding error, so agreement is bounded by roughly `1e-6` relative
**regardless of how precise the C++ build is**. Setting the round-trip tolerance from the C++
scalar type rather than from Python's will produce a failure that looks like a bug and is not.

### Deliberately not written during stage 3

`MpiComm`, `HipBackend`, the radix select and the two-pass histogram selection, and any blocking or
vectorisation tuning beyond the row-blocked loop structure. Stage 3 establishes correctness;
stage 4 measures, and only then is anything optimised.