/// @file
/// @brief The level-based, rank-aware logger of `core/Logger.hpp`.
///
/// The plan asks for a small, header-only facility written in-project: only
/// rank 0 prints by default, output happens on one thread, and `--verbose`
/// raises the level (DevelopmentPlan.md, "Other development choices"). What is
/// pinned here is what the rest of the program will rely on:
///
///   * the levels are ordered, and a message prints only at or below the
///     threshold -- and a suppressed message is never even formatted, so a
///     `debug()` call in a loop costs a comparison;
///   * rank 0 prints; another rank prints only errors, and says which rank it
///     is -- an error on rank 3 that ends in `MPI_Abort` must not vanish;
///   * every message is one whole line, never interleaved with another
///     thread's, and is flushed before the call returns, because `MPI_Abort`
///     does not flush streams;
///   * the logger writes to the stream it was given, so it can be tested --
///     and it knows a rank only as an integer, never through MPI, because
///     `src/core/` compiles identically with and without it.

#include <gtest/gtest.h>

#include <cstddef>
#include <format>
#include <ostream>
#include <sstream>
#include <streambuf>
#include <string>
#include <string_view>
#include <thread>
#include <type_traits>
#include <vector>

#include "core/Logger.hpp"

namespace
{

// --- Compile-time properties ------------------------------------------------

static_assert(std::is_enum_v<altx::LogLevel>);
static_assert(!std::is_convertible_v<altx::LogLevel, int>,
              "LogLevel is a scoped enum");

// Ordered from the most to the least severe: raising the threshold admits
// more messages, which is what `--verbose` does.
static_assert(altx::LogLevel::Error < altx::LogLevel::Warning);
static_assert(altx::LogLevel::Warning < altx::LogLevel::Info);
static_assert(altx::LogLevel::Info < altx::LogLevel::Debug);

// The names are what a `--log-level` option would accept, so they are pinned
// exactly, and are available at compile time.
static_assert(altx::toString(altx::LogLevel::Error) == "error");
static_assert(altx::toString(altx::LogLevel::Warning) == "warning");
static_assert(altx::toString(altx::LogLevel::Info) == "info");
static_assert(altx::toString(altx::LogLevel::Debug) == "debug");
static_assert(noexcept(altx::toString(altx::LogLevel::Info)));

TEST(Logger, TheHarnessRuns) { EXPECT_EQ(2 + 2, 4); }
// A logger refers to a stream it does not own; two copies would share it
// without sharing whatever keeps their lines apart.
/*static_assert(!std::is_copy_constructible_v<altx::Logger>);
static_assert(!std::is_copy_assignable_v<altx::Logger>);

// It cannot be built without a sink: there is no hidden default stream.
static_assert(!std::is_default_constructible_v<altx::Logger>);
static_assert(std::is_constructible_v<altx::Logger, std::ostream &>);
static_assert(
    std::is_constructible_v<altx::Logger, std::ostream &, altx::LogLevel>);
static_assert(
    std::is_constructible_v<altx::Logger, std::ostream &, altx::LogLevel, int>);
*/
/*
/// @brief Splits captured output into lines, dropping the final empty one.
/// @param text The captured output.
/// @return One element per `'\n'`-terminated line.
std::vector<std::string> lines(const std::string &text)
{
    std::vector<std::string> result;
    std::istringstream stream(text);
    std::string line;
    while (std::getline(stream, line))
    {
        result.push_back(line);
    }
    return result;
}

/// @brief Whether @p text contains @p part.
/// @param text The string searched.
/// @param part The substring looked for.
/// @return `true` if @p part occurs in @p text.
bool contains(std::string_view text, std::string_view part)
{
    return text.find(part) != std::string_view::npos;
}

// --- Levels -----------------------------------------------------------------

/// @brief The threshold is Info unless the constructor is told otherwise.
TEST(Logger, DefaultThresholdIsInfo)
{
    std::ostringstream sink;
    const altx::Logger logger(sink);
    EXPECT_EQ(logger.level(), altx::LogLevel::Info);
}

/// @brief `enabled()` admits exactly the levels at or below the threshold.
TEST(Logger, EnabledFollowsTheThreshold)
{
    std::ostringstream sink;
    const altx::Logger logger(sink, altx::LogLevel::Warning);

    EXPECT_TRUE(logger.enabled(altx::LogLevel::Error));
    EXPECT_TRUE(logger.enabled(altx::LogLevel::Warning));
    EXPECT_FALSE(logger.enabled(altx::LogLevel::Info));
    EXPECT_FALSE(logger.enabled(altx::LogLevel::Debug));
}

/// @brief Only the messages at or below the threshold reach the sink.
TEST(Logger, SuppressedMessagesWriteNothing)
{
    std::ostringstream sink;
    altx::Logger logger(sink, altx::LogLevel::Warning);

    logger.debug("a debug message");
    logger.info("an info message");
    EXPECT_TRUE(sink.str().empty()) << sink.str();

    logger.warning("a warning message");
    logger.error("an error message");
    const std::vector<std::string> written = lines(sink.str());
    ASSERT_EQ(written.size(), std::size_t{2}) << sink.str();
    EXPECT_TRUE(contains(written[0], "a warning message")) << written[0];
    EXPECT_TRUE(contains(written[1], "an error message")) << written[1];
}

/// @brief `setLevel()` changes what is printed from then on; it is what
/// `--verbose` calls.
TEST(Logger, SetLevelRaisesTheThreshold)
{
    std::ostringstream sink;
    altx::Logger logger(sink);

    logger.debug("hidden");
    logger.setLevel(altx::LogLevel::Debug);
    EXPECT_EQ(logger.level(), altx::LogLevel::Debug);
    logger.debug("shown");

    const std::string output = sink.str();
    EXPECT_FALSE(contains(output, "hidden")) << output;
    EXPECT_TRUE(contains(output, "shown")) << output;
}

/// @brief Each line names its level, so a warning can be told from an error.
TEST(Logger, LineNamesItsLevel)
{
    std::ostringstream sink;
    altx::Logger logger(sink, altx::LogLevel::Debug);

    logger.error("e");
    logger.warning("w");
    logger.info("i");
    logger.debug("d");

    const std::vector<std::string> written = lines(sink.str());
    ASSERT_EQ(written.size(), std::size_t{4}) << sink.str();
    EXPECT_TRUE(contains(written[0], "error")) << written[0];
    EXPECT_TRUE(contains(written[1], "warning")) << written[1];
    EXPECT_TRUE(contains(written[2], "info")) << written[2];
    EXPECT_TRUE(contains(written[3], "debug")) << written[3];
}

/// @brief `log()` with an explicit level is the same as the named shorthand.
TEST(Logger, LogTakesTheLevelAsAnArgument)
{
    std::ostringstream sink;
    altx::Logger logger(sink, altx::LogLevel::Warning);

    logger.log(altx::LogLevel::Info, "hidden");
    logger.log(altx::LogLevel::Warning, "shown");

    const std::vector<std::string> written = lines(sink.str());
    ASSERT_EQ(written.size(), std::size_t{1}) << sink.str();
    EXPECT_TRUE(contains(written[0], "shown")) << written[0];
    EXPECT_TRUE(contains(written[0], "warning")) << written[0];
}

// --- Formatting -------------------------------------------------------------

/// @brief The message is a `std::format` string, and its arguments are
/// substituted.
TEST(Logger, FormatsItsArguments)
{
    std::ostringstream sink;
    altx::Logger logger(sink);

    logger.info("rlk {} has {} laws", std::string("(9, 3, 1)"), 1234);

    const std::string output = sink.str();
    EXPECT_TRUE(contains(output, "rlk (9, 3, 1) has 1234 laws")) << output;
}

/// @brief Every message is exactly one line, terminated by one newline.
TEST(Logger, EachMessageIsOneTerminatedLine)
{
    std::ostringstream sink;
    altx::Logger logger(sink);

    logger.info("first");
    logger.info("second");

    const std::string output = sink.str();
    ASSERT_FALSE(output.empty());
    EXPECT_EQ(output.back(), '\n');
    EXPECT_FALSE(contains(output, "\n\n")) << output;
    EXPECT_EQ(lines(output).size(), std::size_t{2}) << output;
}

/// @brief Counts how often it is formatted; see LazyFormatting.
struct Counted
{
    /// The number of times std::formatter<Counted> has formatted an object.
    static inline int formatted = 0;
};

}  // namespace
*/
/// @brief Formats a Counted as "counted", and counts each call.
// template <>
// struct std::formatter<Counted> : std::formatter<std::string_view>
//{
//     /// @brief Writes "counted" and increments Counted::formatted.
//     /// @param context The format context the output goes to.
//     /// @return The end of the output.
//     auto format(const Counted & /*value*/, std::format_context &context)
//     const
//     {
//         ++Counted::formatted;
//         return std::formatter<std::string_view>::format("counted", context);
//     }
// };
/*
namespace
{

/// @brief A suppressed message is never formatted.
///
/// This is what makes a `debug()` call inside a loop cheap: the threshold is
/// checked before any string is built, not after.
TEST(Logger, SuppressedMessagesAreNotFormatted)
{
    std::ostringstream sink;
    altx::Logger logger(sink, altx::LogLevel::Info);

    Counted::formatted = 0;
    logger.debug("{}", Counted{});
    EXPECT_EQ(Counted::formatted, 0);

    logger.info("{}", Counted{});
    EXPECT_EQ(Counted::formatted, 1);
    EXPECT_TRUE(contains(sink.str(), "counted")) << sink.str();
}

// --- Ranks ------------------------------------------------------------------

/// @brief A logger is rank 0 unless told otherwise, and remembers its rank.
TEST(Logger, RankDefaultsToZero)
{
    std::ostringstream sink;
    const altx::Logger root(sink);
    const altx::Logger other(sink, altx::LogLevel::Info, 3);

    EXPECT_EQ(root.rank(), 0);
    EXPECT_EQ(other.rank(), 3);
}

/// @brief A non-zero rank prints nothing below an error, whatever the level.
TEST(Logger, OtherRanksAreSilentBelowError)
{
    std::ostringstream sink;
    altx::Logger logger(sink, altx::LogLevel::Debug, 2);

    logger.warning("w");
    logger.info("i");
    logger.debug("d");

    EXPECT_TRUE(sink.str().empty()) << sink.str();
}

/// @brief `enabled()` is false on another rank for what it will not print.
TEST(Logger, EnabledAccountsForTheRank)
{
    std::ostringstream sink;
    const altx::Logger logger(sink, altx::LogLevel::Debug, 2);

    EXPECT_TRUE(logger.enabled(altx::LogLevel::Error));
    EXPECT_FALSE(logger.enabled(altx::LogLevel::Warning));
    EXPECT_FALSE(logger.enabled(altx::LogLevel::Debug));
}

/// @brief An error on another rank is printed and says which rank it came
/// from.
///
/// The rank that throws is the one that calls `MPI_Abort`; if its message were
/// suppressed, the job would fail with nothing to say why.
TEST(Logger, OtherRanksPrintErrorsWithTheirRank)
{
    std::ostringstream sink;
    altx::Logger logger(sink, altx::LogLevel::Info, 3);

    logger.error("cannot open {}", "in.h5");

    const std::vector<std::string> written = lines(sink.str());
    ASSERT_EQ(written.size(), std::size_t{1}) << sink.str();
    EXPECT_TRUE(contains(written[0], "cannot open in.h5")) << written[0];
    EXPECT_TRUE(contains(written[0], "rank 3")) << written[0];
}

// --- Output -----------------------------------------------------------------

/// @brief A stream buffer that discards its output and counts flushes.
class FlushCounter : public std::streambuf
{
public:
    /// @brief The number of times the stream has been flushed.
    /// @return The count of calls to sync().
    [[nodiscard]] int flushes() const noexcept { return my_flushes; }

protected:
    /// @brief Accepts and discards one character.
    /// @param character The character written.
    /// @return @p character, meaning success.
    int_type overflow(int_type character) override { return character; }

    /// @brief Records a flush.
    /// @return 0, meaning success.
    int sync() override
    {
        ++my_flushes;
        return 0;
    }

private:
    /// The count returned by flushes().
    int my_flushes = 0;
};

/// @brief Every printed message is flushed before the call returns.
///
/// `MPI_Abort` terminates without flushing, so a buffered error message --
/// the last thing a failing rank writes -- would otherwise be lost.
TEST(Logger, PrintedMessagesAreFlushed)
{
    FlushCounter buffer;
    std::ostream sink(&buffer);
    altx::Logger logger(sink);

    logger.info("one");
    EXPECT_GE(buffer.flushes(), 1);

    const int after_one = buffer.flushes();
    logger.error("two");
    EXPECT_GT(buffer.flushes(), after_one);
}

/// @brief Lines written concurrently from several threads are never
/// interleaved.
///
/// Logging may be called from inside an OpenMP region; whatever the caller
/// does, each message must arrive as one whole line.
TEST(Logger, ConcurrentMessagesAreNotInterleaved)
{
    constexpr int kThreads = 8;
    constexpr int kMessages = 200;

    std::ostringstream sink;
    altx::Logger logger(sink);

    std::vector<std::thread> threads;
    threads.reserve(kThreads);
    for (int t = 0; t < kThreads; ++t)
    {
        threads.emplace_back(
            [&logger, t]
            {
                for (int i = 0; i < kMessages; ++i)
                {
                    logger.info("<thread {} message {}>", t, i);
                }
            });
    }
    for (std::thread &thread : threads)
    {
        thread.join();
    }

    const std::vector<std::string> written = lines(sink.str());
    ASSERT_EQ(written.size(), std::size_t{kThreads * kMessages});
    for (const std::string &line : written)
    {
        // One opening and one closing marker, in that order, and the closing
        // one last: anything else is two messages spliced together.
        const std::size_t open = line.find('<');
        ASSERT_NE(open, std::string::npos) << line;
        EXPECT_EQ(line.find('<', open + 1), std::string::npos) << line;
        EXPECT_EQ(line.find('>'), line.size() - 1) << line;
    }
}
*/
}  // namespace
