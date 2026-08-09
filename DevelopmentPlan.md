# Altx-cpp

This project is the C++ implementation of the [Altx](https://github.com/halmosb/altx) Python package. The functionality of the code should be the same; however, this implementation will use many high-performance computing libraries (OpenMP, MPI, HIP) in C++.

The purpose of this document is to plan the development of the code: before the start of the development the development tools (vim, git, Github CI/CI, Google Tests, CMake, Docker) and libraries (OpenMP, MPI, HIP, HDF5) are defined.

## Goal of the code
The code should implement the Altx algorithm by first extracting the laws from the training set, then it should transform the transform set.

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

The multi-dimensional arrays will be stored as a Tensor object, which is a wrapper around the std::vector, which handles the manual indexing.

## CMake
For compiling the code CMake will be used.

## Google Tests
For the testing Google Tests will be used. There should be both unit tests and integration tests. Experimenting with test driven development might also be employed.

## Development stages
0. (current) Planning the code structure and development process.
1. Setting up development environment: creating first docker images with vim and VS Code where autocomplete is set-up.
2. Creating the serial version of the code.
3. Profiling the serial version. Creating another project with generated synthetic dataset and profiling with which the different versions can be compared.
3. Creating and profiling MPI version.
4. Creating and profiling HIP version.