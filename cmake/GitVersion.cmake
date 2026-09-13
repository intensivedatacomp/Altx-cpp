# The one place `git` is asked what this build is.
#
# Written as a function rather than as inline code because it is called from
# two contexts that share nothing else: cmake/Version.cmake, during configure,
# and cmake/GenerateVersionHeader.cmake, in script mode on every build. Two
# copies of this would be two answers to "what commit is this".

include_guard(GLOBAL)

function(altx_git_version source_dir out_version out_hash out_dirty)
    #[[Describe the working tree at `source_dir`.

    Sets, in the caller's scope:

      `out_version`  `git describe --tags --dirty --always`, or `unknown`
      `out_hash`     the full commit hash, or `unknown`
      `out_dirty`    `true` or `false`, as a C++ boolean literal

    A tarball with no .git, or a machine with no git, yields `unknown` rather
    than a configure failure: the build must still work, and `unknown` in the
    provenance of an output file is an honest answer, which a plausible-looking
    stale hash would not be.
    ]]
    set(version "unknown")
    set(hash "unknown")
    set(dirty "false")

    find_package(Git QUIET)
    if(GIT_FOUND AND EXISTS "${source_dir}/.git")
        execute_process(
            COMMAND "${GIT_EXECUTABLE}" describe --tags --dirty --always
            WORKING_DIRECTORY "${source_dir}"
            OUTPUT_VARIABLE described
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET
            RESULT_VARIABLE described_result
        )
        if(described_result EQUAL 0 AND described)
            set(version "${described}")
        else()
            # There is a .git here and git still would not answer. Silence
            # would be the wrong response: the fallback below stamps every
            # output file of this build with `unknown` provenance, and the one
            # case that actually happens -- `COPY . .` in a Dockerfile leaving
            # the repository owned by a different user than the build runs as,
            # so git refuses it as "dubious ownership" -- looks exactly like a
            # successful build from the outside.
            message(
                WARNING
                "git is present and ${source_dir}/.git exists, but `git "
                "describe` failed; this build will be labelled with an unknown "
                "version and an unknown commit. In a container, this is "
                "usually `git config --global --add safe.directory "
                "${source_dir}`."
            )
        endif()

        execute_process(
            COMMAND "${GIT_EXECUTABLE}" rev-parse HEAD
            WORKING_DIRECTORY "${source_dir}"
            OUTPUT_VARIABLE revision
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET
            RESULT_VARIABLE revision_result
        )
        if(revision_result EQUAL 0 AND revision)
            set(hash "${revision}")
        endif()

        # Tracked files only, which is what `describe --dirty` also considers:
        # an untracked scratch file in the working tree does not change the
        # binary and must not label the results as unreproducible.
        execute_process(
            COMMAND "${GIT_EXECUTABLE}" status --porcelain --untracked-files=no
            WORKING_DIRECTORY "${source_dir}"
            OUTPUT_VARIABLE status_output
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET
        )
        if(status_output)
            set(dirty "true")
        endif()
    endif()

    set(${out_version} "${version}" PARENT_SCOPE)
    set(${out_hash} "${hash}" PARENT_SCOPE)
    set(${out_dirty} "${dirty}" PARENT_SCOPE)
endfunction()
