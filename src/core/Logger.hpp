#pragma once

#include <string>

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
