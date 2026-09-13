# The altx command line tool.
#
# Every decision in this file and in cmake/ is argued in DevelopmentPlan.md
# under "CMake"; the four that shape the file itself:
#
#   * Nothing is directory-scoped. There is no `include_directories`, no
#     `link_libraries` and no `add_definitions` anywhere in this project --
#     those apply to every target created afterwards, which is how the earlier
#     ALT-CPP attempt ended up with the tests silently inheriting the
#     application's link line. Cross-cutting concerns are INTERFACE targets
#     (`altx_warnings`, `altx_sanitizers`, `altx_coverage`, `altx_config`,
#     `altx_version`) that a target has to ask for by linking it.
#   * There is one library target, and MPI and HIP add *files* to it rather
#     than copies of it. That library does not exist yet -- the repository
#     currently holds one `main` and nothing else -- so `apps/` is presently
#     the only consumer of the interface targets below. `altx_core` arrives
#     with src/, at which point it links exactly the same set.
#   * The options are orthogonal, and an unsupported combination fails at
#     configure time with a readable message instead of producing a subtly
#     wrong build. See cmake/AltxOptions.cmake.
#   * The version is derived from git at *build* time, never written down. See
#     cmake/Version.cmake.
#
# The unit of build is a preset, and every preset corresponds to a Docker
# image; see CMakePresets.json and docker/images.yaml.

cmake_minimum_required(VERSION 3.25)

# 3.25 is the floor for preset schema 6 and for `FetchContent_Declare(SYSTEM)`,
# and Ubuntu 24.04 -- which every image in docker/ is built from -- ships 3.28,
# so it costs nothing.
#
# No VERSION here: `git describe` is the single source of the version number
# (cmake/Version.cmake), and a literal in this call would be a second one that
# nothing keeps in agreement with the first.
project(
    altx
    DESCRIPTION "Adaptive Law-Based Transformation for time series"
    HOMEPAGE_URL "https://github.com/intensivedatacomp/Altx-cpp"
    LANGUAGES CXX
)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_BINARY_DIR)
    message(
        FATAL_ERROR
        "In-source builds are not supported: they scatter build output through "
        "the working tree, where .gitignore only approximately catches it. "
        "Use a preset, which puts the build under build/<preset>/:\n"
        "    cmake --preset cpu-omp-release"
    )
endif()

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

# `CMAKE_CXX_EXTENSIONS` is the initialiser of a per-target property and has no
# INTERFACE form, so it is the one setting here that cannot be attached to a
# target. `-std=c++20` rather than `-std=gnu++20`: a GNU extension that
# compiles on GCC and not on hipcc is a portability failure discovered on the
# machine that has the GPU. The standard *level* is target-scoped, on
# `altx_config`.
set(CMAKE_CXX_EXTENSIONS OFF)

# A configure without a build type produces an unoptimised, undefined-NDEBUG
# build that looks like a release. Every preset sets one; this covers a
# configure by hand.
get_property(ALTX_MULTI_CONFIG GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
if(NOT ALTX_MULTI_CONFIG AND NOT CMAKE_BUILD_TYPE)
    message(STATUS "No CMAKE_BUILD_TYPE given, defaulting to Release")
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
    set_property(
        CACHE CMAKE_BUILD_TYPE
        PROPERTY STRINGS Debug Release RelWithDebInfo MinSizeRel
    )
endif()

include(GNUInstallDirs)
include(AltxOptions)
include(CompilerWarnings)
include(Sanitizers)
include(Coverage)
include(Dependencies)
include(Version)

add_subdirectory(apps)

if(ALTX_ENABLE_TESTS)
    # At the top level, not inside tests/: `enable_testing()` has to run in the
    # top-level directory for CTest to pick the tests up at all.
    enable_testing()
    add_subdirectory(tests)
endif()

altx_print_configuration_summary()
