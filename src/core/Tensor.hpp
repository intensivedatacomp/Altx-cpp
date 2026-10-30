/// @file
/// @brief An owning, row-major, aligned multi-dimensional array:
/// altx::Tensor and its host allocator policy, altx::HostAlloc.
///
/// Every multi-dimensional array in the program -- the embedded windows, the
/// laws `P`, the features -- is one of these (DevelopmentPlan.md, "Storing
/// tensors"). It is deliberately *not* a wrapper around `std::vector`: device
/// memory cannot live in one, and host memory must be aligned for BLAS and
/// SIMD. The properties the rest of the program relies on, all pinned by
/// tests/unit/test_tensor.cpp:
///
///   * **One contiguous, row-major block.** The last index varies fastest, so
///     `(m, l, n_laws)` gives every channel a contiguous `l x n_laws` matrix
///     with leading dimension `n_laws`, which BLAS takes without a copy (rule
///     2 of "The ALTX file").
///   * **Memory comes from a policy.** `Tensor<T, Alloc>` obtains and returns
///     its block only through `Alloc`, once each way, so a `HipAlloc` can be
///     added later without touching `Tensor`. `data()` is the raw pointer that
///     MPI, HDF5 and BLAS need, whatever the policy.
///   * **Zeroed on construction.** Raw memory may hold a NaN, and NaN is not a
///     legal value.
///   * **Move-only.** A tensor may hold gigabytes, or device memory that
///     `memcpy` cannot reach, so an accidental copy does not compile; a move
///     hands over the pointer and allocates nothing.
///   * **Unchecked and checked access.** `operator()` is `noexcept` and
///     unchecked, for kernels; `at()` validates and throws altx::Error.

#pragma once

#include <algorithm>
#include <array>
#include <concepts>
#include <cstddef>
#include <format>
#include <functional>
#include <initializer_list>
#include <limits>
#include <new>
#include <numeric>
#include <span>
#include <type_traits>
#include <utility>
#include <vector>

#include "core/Exceptions.hpp"

namespace altx
{

/// @brief The allocator policy for host memory: 64-byte aligned blocks.
///
/// A policy is a type, never an object: Tensor calls its static members and
/// holds no instance. It speaks in bytes, as both `operator new` and
/// `hipMalloc` do, so a device policy can have the same shape.
struct HostAlloc
{
    /// @brief The alignment of every block, in bytes: one cache line, and the
    /// width of an AVX-512 register.
    static constexpr std::size_t alignment = 64;

    /// @brief Obtains an uninitialised block aligned to #alignment.
    /// @param bytes The size of the block; any value, not only a multiple of
    ///   #alignment.
    /// @return The block, never null.
    /// @throws std::bad_alloc If the memory cannot be obtained.
    static void *allocate(std::size_t bytes)
    {
        return ::operator new(bytes, std::align_val_t(alignment));
    }

    /// @brief Returns a block obtained from allocate().
    /// @param pointer The block, exactly as allocate() returned it.
    /// @param bytes The size it was allocated with. Unused here, since the
    ///   aligned `operator delete` does not need it, but part of the policy
    ///   interface for allocators that do.
    static void deallocate(void *pointer,
                           std::size_t bytes [[maybe_unused]]) noexcept
    {
        ::operator delete(pointer, std::align_val_t(alignment));
    }
};

/// @brief An owning, row-major multi-dimensional array whose rank is chosen
/// at run time.
///
/// The elements are one contiguous block of size() values; the shape is kept
/// beside it. Element `(i0, i1, ..., i(r-1))` of a tensor of shape
/// `(d0, d1, ..., d(r-1))` is at offset
/// `((i0 * d1 + i1) * d2 + i2) * ... + i(r-1)` from data().
///
/// A tensor is either *empty* -- default-constructed, moved from, or with an
/// extent of zero -- or owns exactly one block obtained from @p Alloc.
///
/// @tparam T The element type; `Real` for the algorithm.
/// @tparam Alloc The allocator policy: static `allocate(bytes)` and
///   `deallocate(pointer, bytes) noexcept`. HostAlloc by default.
template <typename T>
concept arithmetic = requires { requires std::is_arithmetic_v<T>; };

template <typename T, typename Alloc = HostAlloc>
    requires arithmetic<T>
class Tensor
{
public:
    /// @brief The element type.
    using value_type = T;
    /// @brief The allocator policy the memory comes from.
    using allocator_type = Alloc;

    /// @brief An empty tensor: rank 0, no elements, no memory.
    Tensor() noexcept = default;

    /// @brief Not copyable: a copy could be gigabytes, or device memory.
    Tensor(const Tensor &) = delete;
    /// @brief Not copyable: a copy could be gigabytes, or device memory.
    /// @return Never; deleted.
    Tensor &operator=(const Tensor &) = delete;

    /// @brief Takes over the buffer and shape of @p other, allocating nothing.
    /// @param other The tensor to move from; left empty.
    Tensor(Tensor &&other) noexcept
        : my_data(std::exchange(other.my_data, nullptr)),
          my_shape(std::exchange(other.my_shape, {}))
    {
    }

    /// @brief Releases this tensor's buffer, then takes over @p other's.
    /// @param other The tensor to move from; left empty.
    /// @return `*this`.
    Tensor &operator=(Tensor &&other) noexcept
    {
        if (this != &other)
        {
            release();
            my_data = std::exchange(other.my_data, nullptr);
            my_shape = std::exchange(other.my_shape, {});
        }
        return *this;
    }

    /// @brief A zero-filled tensor of a shape written out in the code, as in
    /// `Tensor<double> t{2, 3, 4};`.
    /// @param shape The extent of each dimension, outermost first.
    /// @throws altx::Error As for the span constructor.
    explicit Tensor(std::initializer_list<std::size_t> shape)
        : Tensor(std::span<const std::size_t>(shape.begin(), shape.size()))
    {
    }

    /// @brief A zero-filled tensor of a shape known only at run time, such as
    /// one read from a file.
    /// @param shape The extent of each dimension, outermost first. It is
    ///   copied; the tensor does not refer to the caller's storage. An extent
    ///   of zero is legal and gives an empty tensor.
    /// @throws altx::Error If @p shape is empty, or the element or byte count
    ///   does not fit in a `std::size_t`.
    /// @throws std::bad_alloc If the allocator policy cannot obtain the memory.
    explicit Tensor(std::span<const std::size_t> shape)
    {
        check_input_size(shape);

        my_shape.assign(shape.begin(), shape.end());
        my_data =
            static_cast<T *>(allocator_type::allocate(size() * sizeof(T)));

        fill(T{});
    }

    /// @brief Returns the buffer to the allocator policy, if there is one.
    ~Tensor() { release(); }

    /// @brief The number of dimensions.
    /// @return The length of shape(); 0 for a default-constructed or
    ///   moved-from tensor.
    std::size_t rank() const noexcept { return my_shape.size(); }

    /// @brief The extent of each dimension, outermost first.
    /// @return A view of the tensor's own copy, valid until the tensor is
    ///   moved from or destroyed.
    std::span<const std::size_t> shape() const noexcept { return my_shape; }

    /// @brief The number of elements.
    /// @return The product of the extents, or 0 for a tensor of rank 0.
    std::size_t size() const noexcept
    {
        return empty() ? 0
                       : std::reduce(my_shape.begin(), my_shape.end(),
                                     static_cast<std::size_t>(1),
                                     std::multiplies<std::size_t>());
    }

    /// @brief Whether the tensor has no elements.
    /// @return `true` for rank 0 or any extent of zero.
    bool empty() const noexcept
    {
        if (std::find(my_shape.begin(), my_shape.end(), 0) != my_shape.end())
        {
            return true;
        }
        return rank() == 0;
    }

    /// @brief The first element, for MPI, HDF5 and BLAS.
    /// @return A pointer aligned as the policy promises; null for an empty
    ///   tensor that owns no block.
    T *data() noexcept { return my_data; }
    /// @brief The first element, read-only.
    /// @return As for the non-const overload, but read-only.
    const T *data() const noexcept { return my_data; }

    /// @brief Every element, in row-major order.
    /// @return A view of size() elements starting at data().
    std::span<T> values() noexcept { return std::span<T>(my_data, size()); }
    /// @brief Every element, in row-major order, read-only.
    /// @return A read-only view of size() elements starting at data().
    std::span<const T> values() const noexcept
    {
        return std::span<const T>(my_data, size());
    }

    /// @brief The element at the given indices, unchecked; for kernels.
    /// @tparam I Any integral types.
    /// @param idx One index per dimension, outermost first. Their number must
    ///   equal rank() and each must be below its extent; nothing checks this.
    /// @return A reference to the element.
    template <std::integral... I>
    T &operator()(I... idx) noexcept
    {
        return my_data[calculate_offset(idx...)];
    }
    /// @brief The element at the given indices, unchecked and read-only.
    /// @tparam I Any integral types.
    /// @param idx As for the non-const overload.
    /// @return A read-only reference to the element.
    template <std::integral... I>
    const T &operator()(I... idx) const noexcept
    {
        return my_data[calculate_offset(idx...)];
    }

    /// @brief The element at the given indices, checked.
    /// @tparam I Any integral types.
    /// @param idx One index per dimension, outermost first.
    /// @return A reference to the element.
    /// @throws altx::Error If the number of indices differs from rank(), or
    ///   an index -- negative ones included -- is not below its extent.
    template <std::integral... I>
    T &at(I... idx)
    {
        validate_indices(idx...);
        return my_data[calculate_offset(idx...)];
    }
    /// @brief The element at the given indices, checked and read-only.
    /// @tparam I Any integral types.
    /// @param idx As for the non-const overload.
    /// @return A read-only reference to the element.
    /// @throws altx::Error As for the non-const overload.
    template <std::integral... I>
    const T &at(I... idx) const
    {
        validate_indices(idx...);
        return my_data[calculate_offset(idx...)];
    }

    /// @brief Sets every element to @p value.
    /// @param value The value to store.
    void fill(const T &value) noexcept
    {
        std::size_t tensor_size = size();
        for (std::size_t i = 0; i < tensor_size; i++)
        {
            my_data[i] = value;
        }
    }

private:
    /// @brief The block from the allocator policy, or null when the tensor
    /// owns none.
    T *my_data = nullptr;
    /// @brief The extent of each dimension; empty for rank 0.
    std::vector<std::size_t> my_shape;

    /// @brief The row-major offset of an element from data().
    /// @tparam I Any integral types.
    /// @param idx One index per dimension, outermost first; not checked.
    /// @return The sum of each index times the product of the extents to its
    ///   right.
    template <std::integral... I>
    std::size_t calculate_offset(I... idx) const
    {
        std::array<std::size_t, sizeof...(I)> indices = {
            static_cast<std::size_t>(idx)...};
        std::size_t offset = 0;
        for (std::size_t i = 0; i < sizeof...(I); i++)
        {
            offset = (offset * my_shape[i]) + indices[i];
        }
        return offset;
    }

    /// @brief Checks that indices name an element; at() calls it.
    /// @tparam I Any integral types.
    /// @param idx One index per dimension, outermost first. A negative index
    ///   converts to a huge `std::size_t`, and so fails the range check.
    /// @throws altx::Error If the number of indices differs from rank(), or
    ///   an index is not below its extent.
    template <std::integral... I>
    void validate_indices(I... idx) const
    {
        if (empty())
        {
            throw Error("The tensor is empty.");
        }
        if (sizeof...(I) != rank())
        {
            throw Error(
                std::format("The number of indices {} has to be the same as "
                            "the rank {} of the tensor.",
                            sizeof...(I), rank()));
        }

        std::array<std::size_t, sizeof...(I)> indices = {
            static_cast<std::size_t>(idx)...};
        for (std::size_t i = 0; i < sizeof...(I); i++)
        {
            if (indices[i] >= my_shape[i])
            {
                throw Error(std::format(
                    "The required index {} is greater than or equal to the "
                    "size of the tensor {} at index {}",
                    indices[i], my_shape[i], i));
            }
        }
    }

    /// @brief Checks that a shape can be allocated before anything is.
    /// @param shape The requested extents.
    /// @throws altx::Error If @p shape is empty, or the element or byte count
    ///   would overflow a `std::size_t`.
    void check_input_size(std::span<const std::size_t> shape) const
    {
        if (shape.size() == 0)
        {
            throw Error("The shape of the tensor cannot be empty.");
        }

        std::size_t prod = 1;
        for (std::size_t i = 0; i < shape.size(); i++)
        {
            if (shape[i] > std::numeric_limits<std::size_t>::max() / prod)
            {
                throw Error("Too many elements are requested for the tensor.");
            }
            if (shape[i] != 0)
            {
                prod *= shape[i];
            }
        }
        if (prod > std::numeric_limits<std::size_t>::max() / sizeof(T))
        {
            throw Error("Too many bytes are requested for the tensor.");
        }
    }

    /// @brief Returns the block to the policy, if any, and leaves the tensor
    /// empty; safe to call more than once.
    void release() noexcept
    {
        if (my_data != nullptr)
        {
            allocator_type::deallocate(my_data, size() * sizeof(T));
            my_data = nullptr;
            my_shape.clear();
        }
    }
};

}  // namespace altx
