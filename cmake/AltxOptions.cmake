# The build options, and the rule that an unsupported combination fails here
# rather than three hours later in a wrong number.
#
# DevelopmentPlan.md ("Options"): the options are orthogonal by construction,
# and anything that is not must "fail at configure time with a readable
# message". Validation therefore lives next to the declarations, not scattered
# through the files that consume them.
#
# Two options from the plan's table are deliberately absent until something
# reads them, on the principle that an option which does nothing is worse than
# a missing one -- it is a promise the build does not keep:
#
#   * `ALTX_BLAS_VENDOR` arrives with `find_package(BLAS)`, in the milestone
#     that writes src/backend/cpu/LinAlg.hpp. It becomes `BLA_VENDOR`.
#   * the HIP architecture list arrives with the first .hip.cpp source.

include_guard(GLOBAL)

option(ALTX_ENABLE_OPENMP "Compile the CPU backend with OpenMP" ON)
option(ALTX_ENABLE_MPI "Build the MPI communicator and its tests" OFF)
option(ALTX_ENABLE_HIP "Build the HIP backend" OFF)
option(ALTX_ENABLE_TESTS "Build the GoogleTest test binaries" ON)
option(ALTX_ENABLE_COVERAGE "Instrument for gcov/lcov coverage" OFF)
option(ALTX_WERROR "Turn compiler warnings into errors (on in CI)" OFF)
option(ALTX_NATIVE_ARCH "Compile for the building machine's own CPU" OFF)

set(ALTX_SCALAR
    "double"
    CACHE STRING
    "Floating point type the algorithm is instantiated for: double or float"
)
set_property(CACHE ALTX_SCALAR PROPERTY STRINGS double float)

set(ALTX_SANITIZERS
    ""
    CACHE STRING
    "Comma separated sanitizer list, e.g. address,undefined or thread"
)

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

if(NOT ALTX_SCALAR STREQUAL "double" AND NOT ALTX_SCALAR STREQUAL "float")
    message(
        FATAL_ERROR
        "ALTX_SCALAR is '${ALTX_SCALAR}'; it must be 'double' or 'float'. "
        "It selects the single instantiation of `altx::Real` in this binary."
    )
endif()

# `-march=native` was unconditional in ALT-CPP, which was safe only because
# that binary was built and run on one workstation. An image built on a GitHub
# runner and run anywhere else dies with SIGILL on the first instruction the
# runner happened to support, so this is opt-in and the images never set it.
if(ALTX_NATIVE_ARCH)
    message(
        STATUS
        "ALTX_NATIVE_ARCH is ON: the binaries will not be portable off this "
        "machine. Correct for local benchmarking, wrong for an image."
    )
endif()

# Fast math is forbidden, for two independent reasons that are worth having in
# the error message rather than only in the plan:
#
#   * `-ffinite-math-only` lets the compiler assume no NaN can occur, and so
#     deletes the input validation that enforces the no-NaN policy. Every
#     `assert_all_finite()` folds to constant false, and a malformed input
#     becomes silently plausible wrong numbers.
#   * `-fassociative-math` reorders floating point operations, making the
#     result a function of the optimisation level and of whether a loop
#     vectorised. That breaks both differential tests the project is built on:
#     the round trip against the Python fixtures and the CPU/GPU diff.
#
# Checked rather than merely documented because it is most likely to arrive
# through `CXXFLAGS` in someone's environment, where nobody would think to look.
set(ALTX_FORBIDDEN_FLAG_PATTERN
    "-Ofast|-ffast-math|-ffinite-math-only|-fassociative-math|-funsafe-math-optimizations"
)
foreach(
    var
    IN
    ITEMS
        CMAKE_CXX_FLAGS
        CMAKE_CXX_FLAGS_DEBUG
        CMAKE_CXX_FLAGS_RELEASE
        CMAKE_CXX_FLAGS_RELWITHDEBINFO
        CMAKE_CXX_FLAGS_MINSIZEREL
)
    if("${${var}}" MATCHES "${ALTX_FORBIDDEN_FLAG_PATTERN}")
        message(
            FATAL_ERROR
            "${var} contains a fast-math flag (${CMAKE_MATCH_0}), which this "
            "project forbids. -ffinite-math-only deletes the checks that "
            "enforce the no-NaN policy, and -fassociative-math makes results "
            "depend on the optimisation level, breaking the Python round-trip "
            "and the CPU/GPU differential tests. See DevelopmentPlan.md, "
            "'Three compiler-flag decisions'."
        )
    endif()
endforeach()

if(ALTX_ENABLE_COVERAGE AND ALTX_SANITIZERS)
    message(
        FATAL_ERROR
        "ALTX_ENABLE_COVERAGE and ALTX_SANITIZERS cannot be combined: the "
        "sanitizer runtimes intercept the same instrumentation gcov writes "
        "through, and the resulting reports are wrong rather than absent. "
        "Use the `coverage` preset for one and a `*-debug` preset for the other."
    )
endif()

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

function(altx_print_configuration_summary)
    #[[Print the resolved configuration at the end of a configure run.

    Worth the twenty lines because the options are what a build *is*: a
    cpu-omp-release and a cpu-serial-release build differ in one cache
    variable and in nothing an `ls` of the build directory would show.
    ]]
    message(STATUS "")
    message(STATUS "altx configuration")
    message(STATUS "  version .......... ${ALTX_VERSION}")
    message(STATUS "  build type ....... ${CMAKE_BUILD_TYPE}")
    message(
        STATUS
        "  compiler ......... ${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}"
    )
    message(STATUS "  scalar ........... ${ALTX_SCALAR}")
    message(STATUS "  OpenMP ........... ${ALTX_ENABLE_OPENMP}")
    message(STATUS "  MPI .............. ${ALTX_ENABLE_MPI}")
    message(STATUS "  HIP .............. ${ALTX_ENABLE_HIP}")
    message(STATUS "  tests ............ ${ALTX_ENABLE_TESTS}")
    message(STATUS "  coverage ......... ${ALTX_ENABLE_COVERAGE}")
    message(STATUS "  sanitizers ....... ${ALTX_SANITIZERS}")
    message(STATUS "  warnings as errors ${ALTX_WERROR}")
    message(STATUS "  native arch ...... ${ALTX_NATIVE_ARCH}")
    message(STATUS "")
endfunction()
