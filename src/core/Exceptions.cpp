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
IoError::IoError(const string &path, const string &reason)
    : Error(path + ": " + reason),
      my_path(make_shared<string>(path)),
      my_reason(make_shared<string>(reason))
{
}
[[nodiscard]] string_view IoError::path() const noexcept { return *my_path; }
[[nodiscard]] string_view IoError::reason() const noexcept
{
    return *my_reason;
}

FormatError::FormatError(const string &path, const string &reason)
    : Error(path + ": " + reason),
      my_path(make_shared<string>(path)),
      my_reason(make_shared<string>(reason))
{
}
[[nodiscard]] string_view FormatError::path() const noexcept
{
    return *my_path;
}
[[nodiscard]] string_view FormatError::reason() const noexcept
{
    return *my_reason;
}

NonFiniteError::NonFiniteError(size_t instance, size_t channel,
                               size_t time_index)
    : Error(makeMessage(instance, channel, time_index))
{
    my_instance = instance;
    my_channel = channel;
    my_time_index = time_index;
}
[[nodiscard]] size_t NonFiniteError::instance() const noexcept
{
    return my_instance;
}
[[nodiscard]] size_t NonFiniteError::channel() const noexcept
{
    return my_channel;
}
[[nodiscard]] size_t NonFiniteError::timeIndex() const noexcept
{
    return my_time_index;
}

}  // namespace altx
