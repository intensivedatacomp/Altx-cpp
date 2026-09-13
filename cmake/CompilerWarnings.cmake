# `altx_warnings` -- the warning set, as an INTERFACE target.
#
# A target opts in by linking it, which is what keeps the warnings off
# GoogleTest, off HOP and off anything else fetched into the build: those are
# consumed as targets, and a target that is not linked against this one is not
# compiled with these flags. That is the whole reason this is an INTERFACE
# library rather than an append to `CMAKE_CXX_FLAGS`.
#
# `-Werror` is a link-time decision of the same target, so CI turns it on with
# one cache variable and a developer mid-refactor is not fighting the compiler.

include_guard(GLOBAL)

add_library(altx_warnings INTERFACE)
add_library(altx::warnings ALIAS altx_warnings)

set(ALTX_GCC_LIKE_WARNINGS
    -Wall
    -Wextra
    -Wpedantic
    # The numerical ones. This project multiplies index arithmetic by scalar
    # arithmetic on every line of the hot loops, and an implicit narrowing
    # there is exactly the class of bug the Python round-trip test would
    # report as a tolerance failure three milestones later.
    -Wconversion
    -Wsign-conversion
    -Wdouble-promotion
    # Not -Wfloat-equal: the no-NaN policy is enforced by testing a variance
    # against exactly zero *before* dividing by it, and the serial-versus-
    # OpenMP test asserts bitwise equality. Both are exact comparisons on
    # purpose, and a warning that fires on every one of them would be turned
    # off with a pragma at each site rather than heeded.
    # The structural ones.
    -Wshadow
    -Wnon-virtual-dtor
    -Wold-style-cast
    -Wcast-align
    -Woverloaded-virtual
    -Wnull-dereference
    -Wimplicit-fallthrough
    -Wformat=2
    -Wunused
)

set(ALTX_GCC_WARNINGS
    -Wmisleading-indentation
    -Wduplicated-cond
    -Wduplicated-branches
    -Wlogical-op
    -Wuseless-cast
)

# Doxygen catches documentation that is *missing*; -Wdocumentation catches
# documentation that is *wrong* -- a \param naming an argument that no longer
# exists, a documented return on a void function. It is a Clang diagnostic, so
# in a GCC build the check is simply absent, which is why the plan puts a Clang
# job in CI rather than relying on the developer's compiler.
set(ALTX_CLANG_WARNINGS -Wdocumentation -Wdocumentation-pedantic)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU")
    target_compile_options(
        altx_warnings
        INTERFACE ${ALTX_GCC_LIKE_WARNINGS} ${ALTX_GCC_WARNINGS}
    )
elseif(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    # Covers AppleClang and hipcc, both of which report as a Clang variant.
    target_compile_options(
        altx_warnings
        INTERFACE ${ALTX_GCC_LIKE_WARNINGS} ${ALTX_CLANG_WARNINGS}
    )
else()
    message(
        STATUS
        "No warning set for ${CMAKE_CXX_COMPILER_ID}; altx_warnings is empty"
    )
endif()

if(ALTX_WERROR)
    target_compile_options(altx_warnings INTERFACE -Werror)
endif()
