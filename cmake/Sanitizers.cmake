# `altx_sanitizers` -- empty unless ALTX_SANITIZERS names one.
#
# The target exists either way, so that every consumer links it
# unconditionally and no `if()` around a `target_link_libraries` has to be kept
# in agreement with this file.
#
# Sanitizer flags have to reach both the compile and the link line; giving one
# without the other produces undefined references to `__asan_*` that look like
# a broken toolchain rather than a missing flag. `target_link_options` on an
# INTERFACE library is what makes that impossible to get wrong here.

include_guard(GLOBAL)

add_library(altx_sanitizers INTERFACE)
add_library(altx::sanitizers ALIAS altx_sanitizers)

if(ALTX_SANITIZERS)
    if(NOT CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        message(
            FATAL_ERROR
            "ALTX_SANITIZERS is set but ${CMAKE_CXX_COMPILER_ID} does not take "
            "-fsanitize= flags."
        )
    endif()

    string(REPLACE "," ";" ALTX_SANITIZER_LIST "${ALTX_SANITIZERS}")
    set(ALTX_KNOWN_SANITIZERS
        address
        undefined
        thread
        leak
        memory
    )
    foreach(sanitizer IN LISTS ALTX_SANITIZER_LIST)
        if(NOT sanitizer IN_LIST ALTX_KNOWN_SANITIZERS)
            message(
                FATAL_ERROR
                "Unknown sanitizer '${sanitizer}'. ALTX_SANITIZERS takes a "
                "comma separated subset of: ${ALTX_KNOWN_SANITIZERS}."
            )
        endif()
    endforeach()

    # ThreadSanitizer and AddressSanitizer both shadow the whole address space
    # and cannot coexist. The combination is rejected here because the
    # alternative is a link error whose message names neither option.
    if("thread" IN_LIST ALTX_SANITIZER_LIST)
        if(
            "address" IN_LIST ALTX_SANITIZER_LIST
            OR "leak" IN_LIST ALTX_SANITIZER_LIST
        )
            message(
                FATAL_ERROR
                "ALTX_SANITIZERS='${ALTX_SANITIZERS}': thread cannot be "
                "combined with address or leak. They are two builds -- which "
                "is also how the plan schedules them, as separate nightly jobs."
            )
        endif()
    endif()

    # -fno-omit-frame-pointer and -g are what turn a sanitizer report from an
    # address into a stack trace; without them the tool fires correctly and
    # says nothing useful.
    target_compile_options(
        altx_sanitizers
        INTERFACE
            -fsanitize=${ALTX_SANITIZERS}
            -fno-omit-frame-pointer
            -fno-optimize-sibling-calls
            -g
    )
    target_link_options(altx_sanitizers INTERFACE -fsanitize=${ALTX_SANITIZERS})
endif()
