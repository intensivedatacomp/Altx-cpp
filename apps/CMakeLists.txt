# The one application target.
#
# The executable is named `altx` in every configuration. The *binary* names
# that distinguish the builds -- altx-serial, altx-omp, altx-mpi, altx-hip --
# are not target names: they are what docker/runtime.Dockerfile copies the
# output of each preset to, because the difference between them is a cache
# variable and not a target property. See DevelopmentPlan.md, "How one image
# gets two binaries".
#
# There is no library to link yet. When src/ arrives, `altx_core` goes into the
# list below and takes the interface targets with it; this file does not
# otherwise change.

add_executable(altx altx_main.cpp)

target_link_libraries(
    altx
    PRIVATE
        altx::config
        altx::version
        altx::warnings
        altx::sanitizers
        altx::coverage
)

# The deliverable is a command line tool, so the executables are the only thing
# installed: no headers, no library, no export set. ALT-CPP installed a header
# tree for a library nobody linked against.
install(TARGETS altx RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
