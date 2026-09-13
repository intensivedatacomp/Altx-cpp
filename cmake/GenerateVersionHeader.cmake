# Run in script mode (`cmake -P`) by the `altx_version_header` target, on every
# build. See cmake/Version.cmake for why this is a build step and not a
# `configure_file`.
#
# Expects, on the command line:
#   -DALTX_SOURCE_DIR=      the repository root, where git is asked
#   -DALTX_VERSION_INPUT=   src/core/Version.hpp.in
#   -DALTX_VERSION_OUTPUT=  the header to write

cmake_minimum_required(VERSION 3.25)

include("${CMAKE_CURRENT_LIST_DIR}/GitVersion.cmake")

altx_git_version(
    "${ALTX_SOURCE_DIR}"
    ALTX_VERSION
    ALTX_GIT_HASH
    ALTX_GIT_DIRTY
)

# Written beside the real header and moved into place only when the content
# differs. Rewriting it unconditionally would give it a new timestamp on every
# build and rebuild everything that includes it -- which, once provenance is
# written to the output file, is most of the project.
configure_file("${ALTX_VERSION_INPUT}" "${ALTX_VERSION_OUTPUT}.tmp" @ONLY)
execute_process(
    COMMAND
        "${CMAKE_COMMAND}" -E copy_if_different "${ALTX_VERSION_OUTPUT}.tmp"
        "${ALTX_VERSION_OUTPUT}"
    COMMAND_ERROR_IS_FATAL ANY
)
file(REMOVE "${ALTX_VERSION_OUTPUT}.tmp")
