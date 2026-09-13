# The test binaries.
#
# One binary per label rather than per source file: CTest's unit of selection
# is a registered test, not an executable, and `gtest_discover_tests` registers
# each TEST() separately with the label attached. `ctest -L unit` therefore
# works without splitting the build into a dozen link steps.
#
# The labels are the ones CI selects on: unit, integration, mpi, gpu, slow.
# Only `unit` has anything in it so far; the others arrive with the code they
# cover -- `integration` with the CLI, `mpi` through mpiexec wrappers, `gpu`
# with the HIP backend.

include(GoogleTest)

add_executable(altx_unit_tests unit/test_smoke.cpp)

target_link_libraries(
    altx_unit_tests
    PRIVATE
        GTest::gtest_main
        altx::config
        altx::version
        altx::warnings
        altx::sanitizers
        altx::coverage
)

# `DISCOVERY_MODE PRE_TEST` runs the binary to enumerate its tests at `ctest`
# time rather than at build time, which is what keeps the build working when
# the tests cannot run on the build machine -- a sanitizer build under an
# unprivileged container, or a cross-compiled one.
gtest_discover_tests(
    altx_unit_tests
    PROPERTIES LABELS unit
    DISCOVERY_MODE PRE_TEST
)
