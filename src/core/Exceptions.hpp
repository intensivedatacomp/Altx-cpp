#pragma once

#include <sstream>
#include <stdexcept>

namespace altx
{
class Error : public std::runtime_error
{
public:
    Error(std::string what_is_wrong) : std::runtime_error("")
    {
        std::stringstream ss;
        ss << "Error: " << what_is_wrong;
        static_cast<std::runtime_error &>(*this) = std::runtime_error(ss.str());
    }
};
}  // namespace altx
