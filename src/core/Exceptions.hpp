#pragma once

#include <stdexcept>

namespace altx
{
class Error : public std::runtime_error
{
public:
    Error(std::string what_is_wrong) : std::runtime_error("")
    {
        static_cast<std::runtime_error &>(*this) =
            std::runtime_error(what_is_wrong);
    }
};
}  // namespace altx
