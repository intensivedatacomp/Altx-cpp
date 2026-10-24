/// @file
/// @brief A small level-based, rank-aware logger: altx::LogLevel and
/// altx::Logger.
///
/// Written in-project rather than taken from a dependency such as spdlog,
/// consistent with avoiding Boost (DevelopmentPlan.md, "Other development
/// choices"). The properties the rest of the program relies on, all pinned by
/// tests/unit/test_logger.cpp:
///
///   * **An instance, not a global.** A logger writes to a stream it is given
///     and does not own -- `std::cerr` from `main`, a string stream in a test
///     -- and components receive it by reference.
///   * **Rank-aware without MPI.** The rank is a plain `int` handed in by the
///     caller, so `src/core/` stays free of MPI. Rank 0 prints everything up
///     to the threshold; every other rank prints errors only, tagged with its
///     rank, because the rank that fails is the one that calls `MPI_Abort`.
///   * **Lazy.** A suppressed message is never formatted, so a `debug()` call
///     inside a loop costs one comparison.
///   * **One whole line per message, flushed.** Lines from concurrent threads
///     are never spliced together, and each is flushed before the call
///     returns, because `MPI_Abort` does not flush streams.

#pragma once

#include <format>
#include <mutex>
#include <ostream>
#include <string>
#include <string_view>
#include <utility>

namespace altx
{

/// @brief How severe a message is, from the most to the least severe.
///
/// The order is the threshold order: a logger at level `L` prints every
/// message whose level compares `<= L`, so raising the threshold -- what
/// `--verbose` does -- admits more messages. An error is always within the
/// threshold.
enum class LogLevel
{
    Error,    ///< Something failed; the run cannot continue as asked.
    Warning,  ///< Something is suspicious, but the run continues.
    Info,     ///< Progress a user wants to see; the default threshold.
    Debug,    ///< Detail for whoever is debugging the program.
};

/// @brief The name of a level, as a `--log-level` option would spell it.
/// @param level The level to name.
/// @return One of "error", "warning", "info" or "debug", in lower case, or
///   an empty view for a value that is none of the enumerators -- which a
///   scoped enum can hold, since any `int` converts to it. Always a view of a
///   string literal, so it never dangles.
constexpr std::string_view toString(LogLevel level) noexcept
{
    switch (level)
    {
        case LogLevel::Debug:
        {
            return "debug";
        }
        case LogLevel::Info:
        {
            return "info";
        }
        case LogLevel::Warning:
        {
            return "warning";
        }
        case LogLevel::Error:
        {
            return "error";
        }
    }
    return "";
}

/// @brief Writes leveled, rank-tagged messages to a stream it does not own.
///
/// Each message is a `std::format` string with its arguments, checked at
/// compile time. A printed message becomes one line -- the rank tag on a
/// non-zero rank, the level name, then the formatted text -- written whole
/// under a lock and flushed.
///
/// Not copyable: a copy would share the stream without sharing the lock that
/// keeps the lines apart.
class Logger
{
public:
    /// @brief Creates a logger writing to @p sink.
    /// @param sink The stream lines are written to. It is referred to, not
    ///   owned, and must outlive the logger.
    /// @param level The threshold: messages less severe than this are
    ///   suppressed.
    /// @param rank The caller's MPI rank, or 0 when there is no MPI. Only
    ///   rank 0 prints below LogLevel::Error.
    explicit Logger(std::ostream &sink, LogLevel level = LogLevel::Info,
                    int rank = 0)
        : my_sink(sink), my_level(level), my_rank(rank)
    {
    }
    /// @brief Not copyable; see the class description.
    Logger(const Logger &) = delete;
    /// @brief Not copy-assignable; see the class description.
    /// @return Never; the operator is deleted.
    Logger &operator=(const Logger &) = delete;

    /// @brief The current threshold.
    /// @return The least severe level that is still printed on rank 0.
    [[nodiscard]] LogLevel level() const noexcept { return my_level; }
    /// @brief Whether a message at @p level would be printed by this logger.
    ///
    /// Accounts for both the threshold and the rank, so a caller can skip
    /// work whose only purpose is a message that would be suppressed.
    /// @param level The level of the prospective message.
    /// @return `true` on rank 0 for any level within the threshold, and on
    ///   every rank for LogLevel::Error.
    [[nodiscard]] bool enabled(LogLevel level) const noexcept
    {
        return (my_rank == 0 && level <= my_level) ||
               (level == LogLevel::Error);
    }
    /// @brief Formats and prints one message at @p level, if enabled().
    ///
    /// When the message is suppressed, nothing is formatted and nothing is
    /// written. Otherwise the line is built first and only the write is
    /// done under the lock, then flushed.
    /// @tparam Args The types of the format arguments.
    /// @param level The severity of the message.
    /// @param fmt A `std::format` string, checked against @p args at compile
    ///   time.
    /// @param args The values substituted into @p fmt.
    template <class... Args>
    void log(LogLevel level, std::format_string<Args...> fmt, Args &&...args)
    {
        if (enabled(level))
        {
            std::string output = std::format(
                "{}{}: {}",
                rank() != 0 ? "[rank " + std::to_string(my_rank) + "] " : "",
                toString(level), std::format(fmt, std::forward<Args>(args)...));

            const std::lock_guard<std::mutex> guard(my_mutex);
            my_sink << output << std::endl;
        }
    }
    /// @brief log() at LogLevel::Debug.
    /// @tparam Args The types of the format arguments.
    /// @param fmt A `std::format` string.
    /// @param args The values substituted into @p fmt.
    template <class... Args>
    void debug(std::format_string<Args...> fmt, Args &&...args)
    {
        log(LogLevel::Debug, fmt, std::forward<Args>(args)...);
    }
    /// @brief log() at LogLevel::Info.
    /// @tparam Args The types of the format arguments.
    /// @param fmt A `std::format` string.
    /// @param args The values substituted into @p fmt.
    template <class... Args>
    void info(std::format_string<Args...> fmt, Args &&...args)
    {
        log(LogLevel::Info, fmt, std::forward<Args>(args)...);
    }
    /// @brief log() at LogLevel::Warning.
    /// @tparam Args The types of the format arguments.
    /// @param fmt A `std::format` string.
    /// @param args The values substituted into @p fmt.
    template <class... Args>
    void warning(std::format_string<Args...> fmt, Args &&...args)
    {
        log(LogLevel::Warning, fmt, std::forward<Args>(args)...);
    }
    /// @brief log() at LogLevel::Error; printed on every rank.
    /// @tparam Args The types of the format arguments.
    /// @param fmt A `std::format` string.
    /// @param args The values substituted into @p fmt.
    template <class... Args>
    void error(std::format_string<Args...> fmt, Args &&...args)
    {
        log(LogLevel::Error, fmt, std::forward<Args>(args)...);
    }

    /// @brief Changes the threshold for every later message; what
    /// `--verbose` calls.
    ///
    /// Not synchronised: the threshold is read by log() without the lock, so
    /// this must not be called while another thread may be logging through
    /// the same logger. Set it once, before any parallel work starts.
    /// @param level The new threshold.
    void setLevel(LogLevel level) noexcept { my_level = level; }
    /// @brief The rank this logger was created for.
    /// @return The rank given to the constructor; 0 by default.
    [[nodiscard]] int rank() const noexcept { return my_rank; }

private:
    /// The stream lines go to; not owned.
    std::ostream &my_sink;
    /// The threshold; see level().
    LogLevel my_level;
    /// The caller's rank; see rank().
    int my_rank;
    /// Serialises writes to my_sink, so concurrent lines stay whole.
    std::mutex my_mutex;
};

}  // namespace altx
