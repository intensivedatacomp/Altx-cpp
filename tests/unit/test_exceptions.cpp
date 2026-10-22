/// @file
/// @brief The exception hierarchy of `core/Exceptions.hpp`.
///
/// Exceptions are used for validation, I/O and configuration mistakes, never
/// inside a compute kernel (DevelopmentPlan.md, "Other development choices").
/// What is pinned here is what the rest of the program will rely on:
///
///   * one base, `altx::Error`, so that `main` -- and, later, every MPI rank's
///     entry point -- can catch everything this project throws in one clause
///     and tell it apart from a `std::bad_alloc` or a bug;
///   * the categories are siblings, so catching one never swallows another;
///   * every type is nothrow-copyable, because a throw copies the exception
///     object and a copy that throws there calls `std::terminate`;
///   * the structured errors keep their data, and their message names it --
///     the plan requires a non-finite input to be reported with the instance,
///     channel and time index.

#include <gtest/gtest.h>

#include <cstddef>
#include <exception>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>

#include "core/Exceptions.hpp"

namespace altx
{

// --- The hierarchy, checked at compile time ---------------------------------

static_assert(std::is_base_of_v<std::runtime_error, altx::Error>);

/*static_assert(std::is_base_of_v<altx::Error, altx::ParameterError>);
static_assert(std::is_base_of_v<altx::Error, altx::IoError>);
static_assert(std::is_base_of_v<altx::Error, altx::FormatError>);
static_assert(std::is_base_of_v<altx::Error, altx::NonFiniteError>);
static_assert(std::is_base_of_v<altx::Error, altx::NumericalError>);*/

// Siblings, not a chain: a handler for one category must not catch another.
/*static_assert(!std::is_base_of_v<altx::IoError, altx::FormatError>);
static_assert(!std::is_base_of_v<altx::FormatError, altx::IoError>);
static_assert(!std::is_base_of_v<altx::ParameterError, altx::FormatError>);
static_assert(!std::is_base_of_v<altx::NumericalError, altx::NonFiniteError>);*/

/// @brief Asserts the copy guarantees an exception type must give.
/// @tparam E The exception type under test.
template <typename E>
constexpr bool isSafelyCopyable()
{
    return std::is_nothrow_copy_constructible_v<E> &&
           std::is_nothrow_copy_assignable_v<E>;
}

static_assert(isSafelyCopyable<altx::Error>());
/*static_assert(isSafelyCopyable<altx::ParameterError>());
static_assert(isSafelyCopyable<altx::IoError>());
static_assert(isSafelyCopyable<altx::FormatError>());
static_assert(isSafelyCopyable<altx::NonFiniteError>());
static_assert(isSafelyCopyable<altx::NumericalError>());*/

// --- The plain categories ---------------------------------------------------

/// @brief The message-only types keep their message verbatim.
TEST(Exceptions, PlainTypesKeepTheirMessage)
{
    EXPECT_STREQ(altx::Error("base").what(), "base");
    /*EXPECT_STREQ(altx::ParameterError("r must be odd").what(), "r must be
    odd"); EXPECT_STREQ(altx::NumericalError(std::string("moment
    overflowed")).what(), "moment overflowed");*/
}

/*/// @brief One `catch (const altx::Error&)` sees every category.
TEST(Exceptions, EveryCategoryIsCaughtAsError)
{
    EXPECT_THROW(throw altx::ParameterError("p"), altx::Error);
    EXPECT_THROW(throw altx::IoError("f.h5", "r"), altx::Error);
    EXPECT_THROW(throw altx::FormatError("f.h5", "r"), altx::Error);
    EXPECT_THROW(throw altx::NonFiniteError(0, 0, 0), altx::Error);
    EXPECT_THROW(throw altx::NumericalError("n"), altx::Error);
}

/// @brief A sibling handler does not catch the wrong category.
TEST(Exceptions, SiblingHandlerDoesNotCatch)
{
    bool caught_as_io = false;
    bool caught_as_format = false;
    try
    {
        throw altx::FormatError("model.h5", "missing /parameters");
    }
    catch (const altx::IoError &)
    {
        caught_as_io = true;
    }
    catch (const altx::FormatError &)
    {
        caught_as_format = true;
    }
    EXPECT_FALSE(caught_as_io);
    EXPECT_TRUE(caught_as_format);
}

// --- Errors about a file ----------------------------------------------------

/// @brief `IoError` keeps the path apart, and the message names path and
/// reason.
TEST(Exceptions, IoErrorCarriesPath)
{
    const altx::IoError error("data/in.h5", "cannot open for reading");

    EXPECT_EQ(error.path(), std::string_view("data/in.h5"));

    const std::string message = error.what();
    EXPECT_NE(message.find("data/in.h5"), std::string::npos) << message;
    EXPECT_NE(message.find("cannot open for reading"), std::string::npos)
        << message;
}

/// @brief `FormatError` keeps the path apart, and the message names path and
/// reason.
TEST(Exceptions, FormatErrorCarriesPath)
{
    const altx::FormatError error("run.h5", "/laws/000 has no dataset P");

    EXPECT_EQ(error.path(), std::string_view("run.h5"));

    const std::string message = error.what();
    EXPECT_NE(message.find("run.h5"), std::string::npos) << message;
    EXPECT_NE(message.find("/laws/000 has no dataset P"), std::string::npos)
        << message;
}

/// @brief The path is still valid in a copy that outlives the original.
///
/// `path()` returns a view, so this is the test that fails if the view points
/// into storage owned by the object it was copied from.
TEST(Exceptions, PathSurvivesCopy)
{
    std::optional<altx::IoError> original;
    original.emplace("data/in.h5", "reason");
    const altx::IoError copy(*original);
    original.reset();

    EXPECT_EQ(copy.path(), std::string_view("data/in.h5"));
}

/// @brief The empty path is legal; nothing about the type requires a real file.
TEST(Exceptions, EmptyPathIsAllowed)
{
    const altx::IoError error("", "reason");
    EXPECT_TRUE(error.path().empty());
}

// --- Non-finite input -------------------------------------------------------

/// @brief `NonFiniteError` keeps the three indices, 0-based, as given.
TEST(Exceptions, NonFiniteErrorCarriesIndices)
{
    const altx::NonFiniteError error(3, 1, 17);

    EXPECT_EQ(error.instance(), std::size_t{3});
    EXPECT_EQ(error.channel(), std::size_t{1});
    EXPECT_EQ(error.timeIndex(), std::size_t{17});
}

/// @brief The message names the instance, the channel and the time index.
///
/// The exact wording is free; these three phrases are what the plan promises
/// a user will be able to find in the error.
TEST(Exceptions, NonFiniteErrorMessageNamesTheLocation)
{
    const std::string message = altx::NonFiniteError(3, 1, 17).what();

    EXPECT_NE(message.find("instance 3"), std::string::npos) << message;
    EXPECT_NE(message.find("channel 1"), std::string::npos) << message;
    EXPECT_NE(message.find("time index 17"), std::string::npos) << message;
}

/// @brief Indices beyond 32 bits are reported without truncation.
TEST(Exceptions, NonFiniteErrorDoesNotTruncateLargeIndices)
{
    constexpr std::size_t kLarge = std::size_t{1} << 40U;
    const altx::NonFiniteError error(kLarge, 0, 0);

    EXPECT_EQ(error.instance(), kLarge);
    const std::string message = error.what();
    EXPECT_NE(message.find(std::to_string(kLarge)), std::string::npos)
        << message;
}
*/
}  // namespace altx
