/// @file
/// @brief Out-of-line members of the exception types in Exceptions.hpp.
///
/// Defining the constructors here rather than inline gives each class's vtable
/// one home, in this translation unit, instead of a weak copy in every file
/// that includes the header.

#include "core/Exceptions.hpp"

#include <memory>
#include <string>

namespace altx
{
Error::~Error() = default;
ParameterError::~ParameterError() = default;
NumericalError::~NumericalError() = default;

IoError::IoError(const std::string &path, const std::string &reason)
    : Error("I/O error: " + path + ": " + reason),
      my_path(std::make_shared<const std::string>(path)),
      my_reason(std::make_shared<const std::string>(reason))
{
}
std::string_view IoError::path() const noexcept { return *my_path; }
std::string_view IoError::reason() const noexcept { return *my_reason; }

FormatError::FormatError(const std::string &path, const std::string &reason)
    : Error("Format error: " + path + ": " + reason),
      my_path(std::make_shared<const std::string>(path)),
      my_reason(std::make_shared<const std::string>(reason))
{
}
std::string_view FormatError::path() const noexcept { return *my_path; }
std::string_view FormatError::reason() const noexcept { return *my_reason; }

NonFiniteError::NonFiniteError(std::size_t instance, std::size_t channel,
                               std::size_t time_index)
    : Error(makeMessage(instance, channel, time_index)),
      my_instance(instance),
      my_channel(channel),
      my_time_index(time_index)
{
}
std::size_t NonFiniteError::instance() const noexcept { return my_instance; }
std::size_t NonFiniteError::channel() const noexcept { return my_channel; }
std::size_t NonFiniteError::timeIndex() const noexcept { return my_time_index; }

std::string NonFiniteError::makeMessage(std::size_t instance,
                                        std::size_t channel,
                                        std::size_t time_index)
{
    return "Non-finite value at instance " + std::to_string(instance) +
           ", channel " + std::to_string(channel) + ", time index " +
           std::to_string(time_index);
}
}  // namespace altx
