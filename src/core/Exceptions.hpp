/// @file
/// @brief The exceptions this project throws, all derived from altx::Error.
///
/// Exceptions report validation failures, I/O and file-format errors, and
/// configuration mistakes -- things that happen once, outside any loop. They
/// are never thrown from a compute kernel: HIP device code cannot use them, and
/// the CPU and GPU kernels share one structure (DevelopmentPlan.md, "Other
/// development choices").
///
/// Two properties hold for every type here, and tests/unit/test_exceptions.cpp
/// pins both:
///
///   * **One base.** `catch (const altx::Error&)` sees everything the project
///     throws, so `main` -- and every MPI rank's entry point, which must call
///     `MPI_Abort` rather than let an exception hang the job -- can tell a
///     reported error from a `std::bad_alloc` or a bug. The categories below
///     it are siblings, so a handler for one never swallows another.
///   * **Nothrow copy.** A throw copies the exception object, and a copy that
///     throws at that point calls `std::terminate`. Any data carried beside
///     the message is therefore held in shared, immutable storage, never in a
///     plain `std::string` member, whose copy allocates.

#pragma once

#include <cstddef>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>

namespace altx
{
/// @brief The base of every exception this project throws.
///
/// Thrown directly only where no category below fits; catch it to handle
/// everything at once.
class Error : public std::runtime_error
{
public:
    using runtime_error::runtime_error;
    ~Error() override;
};

/// @brief A parameter set violates a semantic rule.
///
/// Thrown by the `(r, l, k)` validation and by `Parameters::validate()`, which
/// runs both on a command line and on a `/parameters` group read from a file.
/// Syntax errors -- an unknown option, a non-integer -- belong to the argument
/// parser and never reach this type.
class ParameterError : public Error
{
public:
    using Error::Error;
    ~ParameterError() override;
};

/// @brief A computation produced a value the numerical policy forbids.
///
/// The pipeline is built so that no operation can create a NaN; this reports
/// the few cases that are detected rather than prevented, such as an
/// `nth_moment` that overflows for large `n` (DevelopmentPlan.md, "Numerical
/// policy").
class NumericalError : public Error
{
public:
    using Error::Error;
    ~NumericalError() override;
};

/// @brief A file could not be opened, read or written.
///
/// For a file that was read but does not conform to the ALTX schema, see
/// FormatError, which is a sibling: a handler for one does not catch the other.
class IoError : public Error
{
public:
    /// @brief Records the file and what went wrong with it.
    /// @param path The file, as the user named it; may be empty.
    /// @param reason What failed, phrased to follow the path, e.g.
    ///   "cannot open for reading".
    ///
    /// `what()` names both, as `path: reason`.
    explicit IoError(const std::string &path, const std::string &reason);

    /// @brief The file the error is about.
    /// @return A view that stays valid as long as any copy of this exception.
    [[nodiscard]] std::string_view path() const noexcept;

    /// @brief What went wrong, without the path.
    /// @return A view that stays valid as long as any copy of this exception.
    [[nodiscard]] std::string_view reason() const noexcept;

private:
    /// Shared between copies, so that copying cannot throw.
    std::shared_ptr<const std::string> my_path;
    /// Shared between copies, so that copying cannot throw.
    std::shared_ptr<const std::string> my_reason;
};

/// @brief A file was read, but it does not conform to the ALTX schema.
///
/// Covers a missing mandatory group, a dataset of the wrong shape, an
/// unsupported `format_version`, and the consistency checks on read, which
/// report which two parts of a file disagree (DevelopmentPlan.md, "Consistency
/// checks on read").
class FormatError : public Error
{
public:
    /// @brief Records the file and the way it violates the schema.
    /// @param path The file, as the user named it; may be empty.
    /// @param reason The violation, e.g. "/laws/000 has no dataset P".
    ///
    /// `what()` names both, as `path: reason`.
    explicit FormatError(const std::string &path, const std::string &reason);

    /// @brief The file the error is about.
    /// @return A view that stays valid as long as any copy of this exception.
    [[nodiscard]] std::string_view path() const noexcept;

    /// @brief The violation, without the path.
    /// @return A view that stays valid as long as any copy of this exception.
    [[nodiscard]] std::string_view reason() const noexcept;

private:
    /// Shared between copies, so that copying cannot throw.
    std::shared_ptr<const std::string> my_path;
    /// Shared between copies, so that copying cannot throw.
    std::shared_ptr<const std::string> my_reason;
};

/// @brief The input data holds a NaN or an infinity.
///
/// NaN is not a legal value anywhere in the pipeline, and input is where it
/// is rejected: the reader scans on load and reports the first offending
/// element, so the user can find it (DevelopmentPlan.md, "Numerical policy").
/// All indices are 0-based, as in the ALTX file.
class NonFiniteError : public Error
{
public:
    /// @brief Records where the offending value is.
    /// @param instance Index of the instance (time series) in the data set.
    /// @param channel Index of the channel within that instance.
    /// @param time_index Index of the sample within that channel.
    explicit NonFiniteError(std::size_t instance, std::size_t channel,
                            std::size_t time_index);

    /// @brief Index of the instance holding the value.
    /// @return The 0-based instance index.
    [[nodiscard]] std::size_t instance() const noexcept;

    /// @brief Index of the channel holding the value.
    /// @return The 0-based channel index.
    [[nodiscard]] std::size_t channel() const noexcept;

    /// @brief Index of the sample within its channel.
    /// @return The 0-based time index.
    [[nodiscard]] std::size_t timeIndex() const noexcept;

private:
    /// @brief Builds the message, before the base class is constructed.
    /// @param instance Index of the instance.
    /// @param channel Index of the channel.
    /// @param time_index Index of the sample.
    /// @return A message naming all three indices.
    static std::string makeMessage(std::size_t instance, std::size_t channel,
                                   std::size_t time_index);
    /// The 0-based instance index.
    std::size_t my_instance;
    /// The 0-based channel index.
    std::size_t my_channel;
    /// The 0-based time index.
    std::size_t my_time_index;
};
}  // namespace altx
