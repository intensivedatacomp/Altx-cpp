/// @file
/// @brief The toolchain runs.
///
/// These two tests assert nothing about the algorithm -- there is none yet.
/// They exist so that milestone 0 of the development plan has the failure it
/// is defined by: a build where CMake configures but GoogleTest is not linked,
/// or where the generated version header never reached the compiler, fails
/// here rather than in the first milestone that has real work to do.

#include <gtest/gtest.h>

#include <string_view>

#include "core/Version.hpp"

namespace
{

/// @brief The test binary builds, links and runs.
TEST(Smoke, TheHarnessRuns) { EXPECT_EQ(2 + 2, 4); }

/// @brief `core/Version.hpp` was generated and reached the compiler.
///
/// A working tree with no git, or a `configure_file` that silently did not
/// run, both surface here: the template substitutes literal values, so an
/// empty string means the generation step produced a header from nothing.
TEST(Smoke, VersionHeaderIsPopulated)
{
    EXPECT_FALSE(altx::kVersion.empty());
    EXPECT_FALSE(altx::kGitHash.empty());
    EXPECT_EQ(altx::kVersion.find('@'), std::string_view::npos)
        << "Version.hpp still contains an unsubstituted placeholder";
}

}  // namespace
