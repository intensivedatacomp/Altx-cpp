# Altx-cpp

This project is the C++ implementation of the [Altx](https://github.com/halmosb/altx) Python package. The functionality of the code should be the same; however, this implementation will use many high-performance computing libraries (OpenMP, MPI, HIP) in C++.

The purpose of this document is to plan the development of the code: before the start of the development the development tools (vim, git, Github CI/CI, Google Tests, CMake, Docker) and libraries (OpenMP, MPI, HIP, HDF5) are defined.

## Goal of the code
The code should implement the Altx algorithm by first extracting the laws from the training set, then it should transform the transform set.

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
|-- docs/                       # Doxyfile.in, mainpage.md
|-- scripts/                    # gen_reference.py, doc and version checks
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

### The ALTX file (`.h5`) -- normative

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
|-- vim/                  # init.lua, plugin list, clangd config
`-- scripts/              # entrypoints, MPI wrapper
```

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

### Vim

The right mechanism is **clangd driven by `compile_commands.json`**, which CMake emits with
`CMAKE_EXPORT_COMPILE_COMMANDS=ON`. Autocomplete and diagnostics for OpenMP, MPI, HDF5 and BLAS
then work with no per-library configuration, because clangd sees the real include paths and
defines of the actual build. Neovim with the built-in LSP client is the proposed editor
configuration, shipped as `docker/vim/`.

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

## Pre-commit? (Similar to pre-commit in Python)
If possible, it should be enforced that the code is formated with clang-format and the documentation, which will be generated with Doxygen, in the code should be checked:
- Every function/class has documentation.
- Every argument is documented.
- Other linting (if possible) e.g. variable cases...
- Check that the version in the argparse, documentation is consistent with the latest git tag.

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
[pre-commit section](#pre-commit-similar-to-pre-commit-in-python) enforceable rather than
aspirational: `WARN_NO_PARAMDOC = YES` plus `WARN_AS_ERROR = YES` turns an undocumented parameter
into a failed build.

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


### Other development choices

## Argument parser
I do not want to use Boost in the project, so the argument parser of the code will be the: [github/p-ranav/argparse](https://github.com/p-ranav/argparse).

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
straight into BLAS. See rule 2 of the model file specification for the derivation.

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

## CMake
For compiling the code CMake will be used.

## Google Tests
For the testing Google Tests will be used. There should be both unit tests and integration tests. Experimenting with test driven development might also be employed.

### Cross-language round-trip test
Because the model file is a contract with the Python implementation, the central integration test
is a round trip:

1. Python trains on a fixed seed and writes an ALTX file containing `/laws`, `/data` and its own
   `/features`.
2. C++ reads that one file for both the laws and the data, transforms, and writes its own
   `/features`.
3. The two feature sets are compared within the tolerance of the build configuration.

Small fixtures (a seeded model and the expected features, a few hundred kilobytes) are committed
to the repository rather than regenerated in CI, so that the C++ test job needs no Python
interpreter and the comparison is deterministic. `scripts/gen_reference.py` regenerates them.

The comparison is on **features, not on laws**. Eigenvectors are defined only up to sign and the
ordering of the laws will differ between implementations, but the algorithm squares the projection
result, so the features are well defined.

### Cross-backend test
The same input is pushed through the CPU and the HIP backend and the outputs are diffed. This is
the reason the backend seam is a runtime choice rather than a compile-time one.

## Development stages
0. (current) Planning the code structure and development process.
1. Setting up development environment: creating first docker images with vim and VS Code where autocomplete is set-up.
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