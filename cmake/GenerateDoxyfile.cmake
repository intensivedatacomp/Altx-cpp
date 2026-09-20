# Run in script mode (`cmake -P`) by the `docs` target, immediately before
# doxygen itself. See docs/CMakeLists.txt for why the Doxyfile is written on
# every build rather than once, by `configure_file`, at configure time.
#
# Script mode shares nothing with the configure that created the build
# directory -- no cache, no `project()`, no variables of any kind -- so every
# value docs/Doxyfile.in substitutes has to arrive on the command line. They
# are all `ALTX_`-prefixed, including the two that mirror CMake's own
# (`ALTX_PROJECT_NAME`, `ALTX_SOURCE_DIR`), so that the template cannot appear
# to read a built-in variable that script mode would define differently --
# `CMAKE_SOURCE_DIR` in particular is the *working directory* here.
#
# Expects, on the command line:
#   -DALTX_SOURCE_DIR=           the repository root: where git is asked, and
#                                what the Doxyfile's paths are built from
#   -DALTX_DOXYFILE_INPUT=       docs/Doxyfile.in
#   -DALTX_DOXYFILE_OUTPUT=      the Doxyfile to write
#   -DALTX_DOXYGEN_OUTPUT_DIR=   where doxygen writes html/
#   -DALTX_PROJECT_NAME=         PROJECT_NAME, from the top-level project()
#   -DALTX_PROJECT_DESCRIPTION=  PROJECT_DESCRIPTION, likewise
#   -DALTX_HAVE_DOT=             whether graphviz was found
#
# ALTX_VERSION is the one value deliberately *not* passed in. Deriving it here,
# on every build, is the entire reason this script exists.

cmake_minimum_required(VERSION 3.25)

include("${CMAKE_CURRENT_LIST_DIR}/GitVersion.cmake")

altx_git_version(
    "${ALTX_SOURCE_DIR}"
    ALTX_VERSION
    ALTX_GIT_HASH
    ALTX_GIT_DIRTY
)

# No `copy_if_different` dance, unlike cmake/GenerateVersionHeader.cmake, which
# needs one because a new timestamp on the version header rebuilds everything
# that includes it. Nothing depends on this file's timestamp: `docs` is a phony
# target that runs doxygen whenever it is asked for, and doxygen decides for
# itself which pages need regenerating.
configure_file("${ALTX_DOXYFILE_INPUT}" "${ALTX_DOXYFILE_OUTPUT}" @ONLY)
