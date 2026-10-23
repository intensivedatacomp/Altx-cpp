#pragma once

#include <string>
#include <string_view>

namespace altx
{

enum class LogLevel
{
    Error,
    Warning,
    Info,
    Debug,
};

class Logger
{
public:
    explicit Logger(std::ostream &sink, LogLevel level = LogLevel::Info,
                    int rank = 0)
        : my_sink(sink), my_level(level), my_rank(rank){};
    Logger(const Logger &) = delete;
    Logger &operator=(const Logger &) = delete;

    LogLevel level() const noexcept { return my_level; }
    bool enabled(LogLevel level) const noexcept
    {
        return my_rank == 0 && level <= my_level;
    }

private:
    std::ostream &my_sink;
    LogLevel my_level;
    int my_rank;
};

constexpr std::string_view toString(LogLevel lev) noexcept
{
    std::string_view ret;
    switch (lev)
    {
        case LogLevel::Debug:
        {
            ret = "debug";
            break;
        }
        case LogLevel::Info:
        {
            ret = "info";
            break;
        }
        case LogLevel::Warning:
        {
            ret = "warning";
            break;
        }
        case LogLevel::Error:
        {
            ret = "error";
            break;
        }
    }
    return ret;
}
}  // namespace altx
