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
|-- external/                   # argparse, fetched by CMake
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
For running the code there should be other smaller images:
- Serial / OpenMP with BLAS and LAPACK.
- MPI
- HIP
- OpenMP / MPI

### Managing image tags

### Vim
Set-up autocomplete and syntax highlighting for the used libraries.

### Debuggers and profiles
There should be debuggers and profiles in the docker images:
- gdb
- Intel VTune profiler
- Score-P

## Pre-commit? (Similar to pre-commit in Python)
If possible, it should be enforced that the code is formated with clang-format and the documentation, which will be generated with Doxygen, in the code should be checked:
- Every function/class has documentation.
- Every argument is documented.
- Other linting (if possible) e.g. variable cases...
- Check that the version in the argparse, documentation is consistent with the latest git tag.

## Github CI/CD

### Build documentation with Doxygen
The HTML documentation of the code should be build automatically with Doxygen. The docker images should be scanned for vulnerabilities.

### Run tests
The tests should be run automatically and code coverage should be calculated.

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
It should be possible to run the code on NVIDIA and on AMD GPUs, so it will be written in HIP.

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
3. Creating the serial version of the code.
4. Profiling the serial version. Creating another project with generated synthetic dataset and profiling with which the different versions can be compared.
5. Creating and profiling MPI version.
6. Creating and profiling HIP version.