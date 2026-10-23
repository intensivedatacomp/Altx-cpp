#pragma once

#include <sstream>
#include <string>
#include <string_view>

namespace altx
{

enum LogLevel
{
    Error,
    Warning,
    Info,
    Debug,
};

class Logger
{
public:
    explicit Logger(std::ostringstream &sink, LogLevel level = LogLevel::Info,
                    int rank = 0)
        : my_level(level){};
    Logger(const Logger &) = delete;
    Logger &operator=(const Logger &) = delete;

    LogLevel level() const noexcept { return my_level; }

private:
    LogLevel my_level;
};

constexpr std::string toString(LogLevel lev) noexcept
{
    std::string ret;
    switch (lev)
    {
        case Debug:
        {
            ret = "debug";
            break;
        }
        case Info:
        {
            ret = "info";
            break;
        }
        case Warning:
        {
            ret = "warning";
            break;
        }
        case Error:
        {
            ret = "error";
            break;
        }
    }
    return ret;
}
}  // namespace altx
