/// @file
/// @brief Out-of-line members of the exception types in Exceptions.hpp.
///
/// Defining the constructors here rather than inline gives each class's vtable
/// one home, in this translation unit, instead of a weak copy in every file
/// that includes the header.

#include "Exceptions.hpp"

#include <memory>
#include <string>

namespace altx
{
Error::~Error() {}
ParameterError::~ParameterError() {}
NumericalError::~NumericalError() {}

IoError::IoError(const std::string &path, const std::string &reason)
    : Error("FileError: " + path + ": " + reason),
      my_path(make_shared<std::string>(path)),
      my_reason(make_shared<std::string>(reason))
{
}
std::string_view IoError::path() const noexcept { return *my_path; }
std::string_view IoError::reason() const noexcept { return *my_reason; }

FormatError::FormatError(const std::string &path, const std::string &reason)
    : Error(path + ": " + reason),
      my_path(make_shared<std::string>(path)),
      my_reason(make_shared<std::string>(reason))
{
}
std::string_view FormatError::path() const noexcept { return *my_path; }
std::string_view FormatError::reason() const noexcept { return *my_reason; }

NonFiniteError::NonFiniteError(size_t instance, size_t channel,
                               size_t time_index)
    : Error(makeMessage(instance, channel, time_index)),
      my_instance(instance),
      my_channel(channel),
      my_time_index(time_index)
{
}
size_t NonFiniteError::instance() const noexcept { return my_instance; }
size_t NonFiniteError::channel() const noexcept { return my_channel; }
size_t NonFiniteError::timeIndex() const noexcept { return my_time_index; }

}  // namespace altx
