# `altx_coverage` -- empty unless ALTX_ENABLE_COVERAGE is on.
#
# Scoped to a target for the reason the whole cmake/ directory is: coverage
# instrumentation on GoogleTest's own sources would report the test framework's
# uncovered branches as this project's, which is how a coverage number stops
# meaning anything.

include_guard(GLOBAL)

add_library(altx_coverage INTERFACE)
add_library(altx::coverage ALIAS altx_coverage)

if(ALTX_ENABLE_COVERAGE)
    if(NOT CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        message(
            FATAL_ERROR
            "ALTX_ENABLE_COVERAGE is ON but ${CMAKE_CXX_COMPILER_ID} does not "
            "support --coverage. The `coverage` preset builds in dev-cpu, "
            "where the compiler is GCC and lcov is installed."
        )
    endif()

    # An optimised coverage build attributes lines to the wrong place: the
    # inliner is free to move them, and gcov reports where the code ended up.
    if(NOT CMAKE_BUILD_TYPE STREQUAL "Debug")
        message(
            WARNING
            "Coverage with CMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}: optimisation "
            "moves lines between functions, so the report will be misleading. "
            "The `coverage` preset uses Debug."
        )
    endif()

    # `--coverage` is the documented spelling for both compile and link, and
    # expands to -fprofile-arcs -ftest-coverage plus -lgcov respectively.
    target_compile_options(altx_coverage INTERFACE --coverage)
    target_link_options(altx_coverage INTERFACE --coverage)
endif()
