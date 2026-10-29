#pragma once

#include <bits/stdc++.h>

#include <algorithm>
#include <concepts>
#include <cstdarg>
#include <cstddef>
#include <cstdlib>
#include <format>
#include <functional>
#include <initializer_list>
#include <limits>
#include <new>
#include <span>
#include <vector>

#include "core/Exceptions.hpp"

namespace altx
{

struct HostAlloc
{
    static constexpr std::size_t alignment = 64;
    static void *allocate(std::size_t bytes)  // throws std::bad_alloc
    {
        return ::operator new(bytes, std::align_val_t(alignment));
    }
    static void deallocate(void *pointer,
                           std::size_t bytes [[maybe_unused]]) noexcept
    {
        ::operator delete(pointer, std::align_val_t(alignment));
    }
};
template <typename T, typename Alloc = HostAlloc>
class Tensor
{
public:
    using value_type = T;
    using allocator_type = Alloc;
    Tensor() noexcept {}
    Tensor(const Tensor &) = delete;
    Tensor &operator=(const Tensor &) = delete;
    Tensor(Tensor &&) noexcept;
    Tensor &operator=(Tensor &&) noexcept;
    explicit Tensor(std::initializer_list<std::size_t> shape)
        : Tensor(std::span<const std::size_t>(shape.begin(), shape.size()))
    {
    }
    explicit Tensor(std::span<const std::size_t> shape)
    {
        check_input_size(shape);

        my_shape.assign(shape.begin(), shape.end());
        my_rank = shape.size();
        my_data =
            static_cast<T *>(allocator_type::allocate(size() * sizeof(T)));

        fill(0);
    }
    ~Tensor() { allocator_type::deallocate(my_data, size()); }
    std::size_t rank() const noexcept { return my_shape.size(); }
    std::span<const std::size_t> shape() const noexcept { return my_shape; }
    std::size_t size() const noexcept
    {
        return empty() ? 0
                       : std::reduce(my_shape.begin(), my_shape.end(),
                                     static_cast<std::size_t>(1),
                                     std::multiplies<std::size_t>());
    }
    bool empty() const noexcept
    {
        if (std::find(my_shape.begin(), my_shape.end(), 0) != my_shape.end())
        {
            return true;
        }
        return my_rank == 0;
    }
    T *data() noexcept { return my_data; }
    const T *data() const noexcept { return my_data; }
    std::span<T> values() noexcept { return std::span<T>(my_data, size()); }
    std::span<const T> values() const
    {
        return std::span<const T>(my_data, size());
    }
    template <std::integral... I>
    T &operator()(I... idx) noexcept
    {
        return my_data[calculate_offset(idx...)];
    }
    template <std::integral... I>
    const T &operator()(I... idx) const noexcept
    {
        return my_data[calculate_offset(idx...)];
    }
    template <std::integral... I>
    T &at(I... idx)
    {
        validate_offset(idx...);
        return my_data[calculate_offset(idx...)];
    }
    template <std::integral... I>
    const T &at(I... idx) const
    {
        validate_offset(idx...);
        return my_data[calculate_offset(idx...)];
    }
    void fill(const T &value) noexcept
    {
        for (std::size_t i = 0; i < size(); i++)
        {
            my_data[i] = value;
        }
    }

private:
    T *my_data = nullptr;
    std::size_t my_rank = 0;
    std::vector<std::size_t> my_shape;
    std::span<T> my_values;

    template <std::integral... I>
    std::size_t calculate_offset(I... idx) const
    {
        std::array<std::size_t, sizeof...(I)> indices = {
            static_cast<std::size_t>(idx)...};
        std::size_t offset = 0;
        for (std::size_t i = 0; i < sizeof...(I) - 1; i++)
        {
            std::size_t loc_offset = 1;
            for (std::size_t j = i; j < sizeof...(I) - 1; j++)
            {
                loc_offset *= my_shape[j + 1];
            }
            offset += loc_offset * indices[i];
        }
        offset += indices[sizeof...(I) - 1];
        return offset;
    }

    template <std::integral... I>
    void validate_offset(I... idx) const
    {
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
                throw Error(
                    std::format("The required index {} is greater than the "
                                "size of the tensor {} at index {}",
                                indices[i], my_shape[i], i));
            }
        }
    }

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
            if (shape[i] * sizeof(T) >
                std::numeric_limits<std::size_t>::max() / (prod * sizeof(T)))
            {
                throw Error("Too many byte are requested for the tensor.");
            }
            if (shape[i] != 0)
            {
                prod *= shape[i];
            }
        }
    }
};

}  // namespace altx
