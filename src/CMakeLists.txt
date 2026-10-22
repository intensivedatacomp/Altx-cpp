# The one library target.
#
# Everything the application and the test binaries share is compiled once,
# here, and linked by both -- so a test exercises the same objects that ship.
# It is internal: never installed, no exported headers (DevelopmentPlan.md,
# "Directory layout").
#
# MPI and HIP will add *files* to this target with `target_sources`, never a
# second library built from a copy of this list. That is the specific mistake
# ALT-CPP made, and "One library, conditional sources" in the plan is the
# argument against it.

add_library(altx_core STATIC core/Exceptions.cpp)
add_library(altx::core ALIAS altx_core)

# Headers live next to their sources and are included by their path below
# src/ -- `#include "core/Exceptions.hpp"` -- the same spelling the generated
# `core/Version.hpp` already uses.
target_include_directories(altx_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

# `config` and `version` are PUBLIC: the standard level and `ALTX_SCALAR` are
# part of what a header means, so a consumer compiled without them would see
# different declarations. The other three are build policy for this target's
# own translation units; every consumer already links them for itself.
target_link_libraries(
    altx_core
    PUBLIC altx::config altx::version
    PRIVATE altx::warnings altx::sanitizers altx::coverage
)
