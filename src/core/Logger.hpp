#pragma once

#include <format>
#include <iostream>
#include <string>
#include <string_view>
#include <utility>

namespace altx
{

enum class LogLevel
{
    Error,
    Warning,
    Info,
    Debug,
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
        return (my_rank == 0 && level <= my_level) ||
               (level == LogLevel::Error);
    }
    template <class... Args>
    void log(LogLevel level, std::format_string<Args...> fmt, Args &&...args)
    {
        if (enabled(level))
        {
            std::string output = "";
            if (my_rank != 0)
            {
                output += "[rank " + std::to_string(my_rank) + "] ";
            }
            output += std::string(toString(level)) + ": " +
                      std::format(fmt, std::forward<Args>(args)...);
            my_sink << output << std::endl;
        }
    }
    template <class... Args>
    void debug(std::format_string<Args...> fmt, Args &&...args)
    {
        log(LogLevel::Debug, fmt, std::forward<Args>(args)...);
    }
    template <class... Args>
    void info(std::format_string<Args...> fmt, Args &&...args)
    {
        log(LogLevel::Info, fmt, std::forward<Args>(args)...);
    }
    template <class... Args>
    void warning(std::format_string<Args...> fmt, Args &&...args)
    {
        log(LogLevel::Warning, fmt, std::forward<Args>(args)...);
    }
    template <class... Args>
    void error(std::format_string<Args...> fmt, Args &&...args)
    {
        log(LogLevel::Error, fmt, std::forward<Args>(args)...);
    }

    void setLevel(LogLevel level) noexcept { my_level = level; }
    int rank() const noexcept { return my_rank; }

private:
    std::ostream &my_sink;
    LogLevel my_level;
    int my_rank;
};

}  // namespace altx
