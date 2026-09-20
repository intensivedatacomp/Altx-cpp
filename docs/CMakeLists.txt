# The `docs` target: Doxygen HTML covering both the API reference extracted
# from src/ and the prose pages in this directory. One site, not two -- see
# DevelopmentPlan.md, "Build documentation with Doxygen".
#
# Not `doxygen_add_docs()`: that helper writes its own Doxyfile from DOXYGEN_*
# variables and ignores Doxyfile.in, which is where every setting the plan
# argues for lives -- `EXTRACT_ALL = NO` above all, since the documentation
# coverage check silently enforces nothing without it.

# REQUIRED, rather than a quiet fallback to no documentation: this directory is
# only added when ALTX_ENABLE_DOCS is on, so somebody asked for docs and wants
# to be told why they are not getting them. `dot` is a component of the same
# request for the same reason -- graph-less output is not what was asked for --
# and docker/dev.Dockerfile installs graphviz alongside doxygen.
find_package(Doxygen REQUIRED dot)

set(ALTX_DOXYFILE_INPUT "${CMAKE_CURRENT_SOURCE_DIR}/Doxyfile.in")

# Both generated, so both under the build directory. A Doxyfile written next to
# its own template in docs/ is one `git add .` away from being committed, and a
# committed generated file is one that somebody edits by hand.
set(ALTX_DOXYFILE "${CMAKE_CURRENT_BINARY_DIR}/Doxyfile")
set(ALTX_DOXYGEN_OUTPUT_DIR "${CMAKE_CURRENT_BINARY_DIR}")

# The Doxyfile is written on every build, by a script, rather than once by
# `configure_file` here -- the same argument cmake/Version.cmake makes for the
# version header, and for the same reason. PROJECT_NUMBER comes from `git
# describe`, and when HEAD is a symref `git commit` rewrites refs/heads/<branch>
# without touching .git/HEAD, so the CMAKE_CONFIGURE_DEPENDS in Version.cmake
# does not fire on a new commit. A configure-time substitution would therefore
# stamp the published site with whatever commit happened to be checked out when
# the build directory was created, and keep doing so until something unrelated
# forced a reconfigure. `--dirty` is not covered by that dependency either.
#
# Not `ALL`: doxygen is seconds of work nobody asked for on an ordinary build.
add_custom_target(
    docs
    COMMAND
        "${CMAKE_COMMAND}" "-DALTX_SOURCE_DIR=${CMAKE_SOURCE_DIR}"
        "-DALTX_DOXYFILE_INPUT=${ALTX_DOXYFILE_INPUT}"
        "-DALTX_DOXYFILE_OUTPUT=${ALTX_DOXYFILE}"
        "-DALTX_DOXYGEN_OUTPUT_DIR=${ALTX_DOXYGEN_OUTPUT_DIR}"
        "-DALTX_PROJECT_NAME=${PROJECT_NAME}"
        "-DALTX_PROJECT_DESCRIPTION=${PROJECT_DESCRIPTION}"
        # `Doxygen_dot_FOUND`, not `DOXYGEN_DOT_FOUND`: the latter is what
        # FindDoxygen's own documentation suggests and it is never set -- the
        # value that exists is CMake's standard `<Package>_<component>_FOUND`,
        # spelled with the component in the case it was requested in. The
        # wrong one expands to the empty string, which doxygen accepts in
        # silence and then interprets as YES.
        "-DALTX_HAVE_DOT=${Doxygen_dot_FOUND}" -P
        "${CMAKE_SOURCE_DIR}/cmake/GenerateDoxyfile.cmake"
    COMMAND "${DOXYGEN_EXECUTABLE}" "${ALTX_DOXYFILE}"
    # Every path in the Doxyfile is absolute, so this changes nothing that
    # doxygen reads. It fixes where doxygen's own relative output would land if
    # a future setting forgets that, and makes the command reproducible by hand.
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    COMMENT "Generating documentation with Doxygen"
    VERBATIM
)
