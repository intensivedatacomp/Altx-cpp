# External dependencies, and `altx_config` -- the target that carries what
# every piece of altx is compiled with.
#
# `find_package` with imported targets throughout. No hardcoded
# `/opt/intel/oneapi` paths, no manual `-Wl,--start-group` lists and no RPATH
# surgery: ALT-CPP needed all three because a conda installation was polluting
# the library search path, and inside the containers there is no conda. That is
# a concrete payoff of building in Docker, and it is taken here by deleting the
# workaround rather than porting it.
#
# Libraries that nothing links yet -- BLAS, LAPACK, HDF5 -- are found in the
# milestone that first needs them, not now. A `find_package` whose result is
# unused is a configure-time failure waiting for a machine that lacks something
# the build does not actually require.

include_guard(GLOBAL)

# ---------------------------------------------------------------------------
# altx_config: language level, configuration macros, and the dependency links
# ---------------------------------------------------------------------------

add_library(altx_config INTERFACE)
add_library(altx::config ALIAS altx_config)

target_compile_features(altx_config INTERFACE cxx_std_20)

# The scalar type is one typedef, chosen at configure time, and there is
# exactly one instantiation per binary -- so `using Real = ALTX_SCALAR;` in
# src/core/ is the whole of it. Templating the algorithm would buy nothing for
# a CLI tool while costing compile time; the BLAS wrapper is the single
# exception and templates itself.
target_compile_definitions(altx_config INTERFACE ALTX_SCALAR=${ALTX_SCALAR})

if(ALTX_NATIVE_ARCH)
    include(CheckCXXCompilerFlag)
    check_cxx_compiler_flag(-march=native ALTX_HAS_MARCH_NATIVE)
    if(NOT ALTX_HAS_MARCH_NATIVE)
        message(
            FATAL_ERROR
            "ALTX_NATIVE_ARCH is ON but ${CMAKE_CXX_COMPILER_ID} rejects "
            "-march=native."
        )
    endif()
    target_compile_options(altx_config INTERFACE -march=native)
endif()

# ---------------------------------------------------------------------------
# OpenMP -- a compile option inside the CPU backend, not a backend
# ---------------------------------------------------------------------------
#
# The `#pragma omp` directives compile to nothing when this is off, so the
# serial and the OpenMP builds are the same source file with no `#ifdef`
# between them. `ALTX_HAS_OPENMP` exists for the few places that need the
# runtime API (`omp_get_max_threads()` in the provenance written to the output
# file), never to select between two versions of the algorithm.

if(ALTX_ENABLE_OPENMP)
    find_package(OpenMP REQUIRED COMPONENTS CXX)
    target_link_libraries(altx_config INTERFACE OpenMP::OpenMP_CXX)
    target_compile_definitions(altx_config INTERFACE ALTX_HAS_OPENMP=1)
endif()

# ---------------------------------------------------------------------------
# MPI
# ---------------------------------------------------------------------------
#
# MPI adds files to `altx_core` (src/dist/MpiComm.cpp) and never a second copy
# of the algorithm. `src/core/` must compile identically with and without this
# option -- an `#ifdef ALTX_HAS_MPI` appearing there is the signal that a
# responsibility has been put in the wrong layer.

if(ALTX_ENABLE_MPI)
    find_package(MPI REQUIRED COMPONENTS CXX)
    target_link_libraries(altx_config INTERFACE MPI::MPI_CXX)
    target_compile_definitions(altx_config INTERFACE ALTX_HAS_MPI=1)
endif()

# ---------------------------------------------------------------------------
# HIP
# ---------------------------------------------------------------------------
#
# `enable_language(HIP)` only under the option, so that a CPU image -- which
# has no hipcc -- configures. The failure below is the "readable message"
# half of the orthogonality rule: dev-cpu ships no ROCm, and
# `ALTX_ENABLE_HIP=ON` there must say so rather than fail later inside a
# compile of something that looks unrelated.

if(ALTX_ENABLE_HIP)
    include(CheckLanguage)
    check_language(HIP)
    if(NOT CMAKE_HIP_COMPILER)
        message(
            FATAL_ERROR
            "ALTX_ENABLE_HIP=ON but no HIP compiler was found. The HIP presets "
            "build inside the dev-gpu image, which ships the ROCm SDK; dev-cpu "
            "deliberately does not. Set CMAKE_HIP_COMPILER if hipcc lives "
            "somewhere non-standard."
        )
    endif()
    enable_language(HIP)
    target_compile_definitions(altx_config INTERFACE ALTX_HAS_HIP=1)
endif()

# ---------------------------------------------------------------------------
# GoogleTest
# ---------------------------------------------------------------------------
#
# Pinned to a commit hash rather than to a tag, which can be moved. No
# `FIND_PACKAGE_ARGS`: falling back to a system GoogleTest would make the
# version a property of the machine, and two machines disagreeing about the
# test framework is the kind of difference that is only noticed once it
# matters.
#
# `SYSTEM` keeps altx_warnings' -Wconversion and -Wold-style-cast off
# GoogleTest's headers, which are included by every test file.
#
# This is the one step of a configure that needs the network. The plan has the
# development image pre-populating `FETCHCONTENT_BASE_DIR` so that a configure
# inside it resolves locally; until that layer exists, an offline configure
# needs either `-DALTX_ENABLE_TESTS=OFF` or
# `-DFETCHCONTENT_SOURCE_DIR_GOOGLETEST=<path to a checkout>`.

if(ALTX_ENABLE_TESTS)
    include(FetchContent)

    # GoogleTest installs its own targets and vendors its own `gtest.pc`
    # otherwise, which would end up in `altx`'s install tree. Nothing but the
    # executables is installed from this project.
    set(INSTALL_GTEST OFF)
    # No mocks are planned: every oracle in this project is either a documented
    # value from the Python docstrings or a second implementation, and neither
    # is mocked.
    set(BUILD_GMOCK OFF)

    FetchContent_Declare(
        googletest
        GIT_REPOSITORY https://github.com/google/googletest.git
        # v1.18.0
        GIT_TAG 063de7e9578f82b369302001269680b4b1553359
        SYSTEM
        EXCLUDE_FROM_ALL
    )
    FetchContent_MakeAvailable(googletest)
endif()
