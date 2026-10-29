#pragma once

#include <bits/stdc++.h>

#include <concepts>
#include <cstddef>
#include <cstdlib>
#include <functional>
#include <initializer_list>
#include <new>
#include <span>
#include <vector>

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
    {
        my_shape.assign(shape.begin(), shape.end());
        my_rank = shape.size();
        my_data =
            static_cast<T *>(allocator_type::allocate(size() * sizeof(T)));
        // my_values = my_data;
    }
    explicit Tensor(std::span<const std::size_t> shape)
    {
        my_shape.assign(shape.begin(), shape.end());
        my_rank = shape.size();
        my_data =
            static_cast<T *>(allocator_type::allocate(size() * sizeof(T)));
        // my_values = my_data;
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
    bool empty() const noexcept { return my_rank == 0; }
    T *data() noexcept;
    const T *data() const noexcept;
    std::span<T> values() noexcept { std::span<const T>(my_data, size()); }
    std::span<const T> values() const
    {
        return std::span<const T>(my_data, size());
    }
    template <std::integral... I>
    T &operator()(I...) noexcept;
    template <std::integral... I>
    const T &operator()(I...) const noexcept;
    template <std::integral... I>
    T &at(I...)
    {
    }
    template <std::integral... I>
    const T &at(I...) const
    {
    }
    void fill(const T &value) noexcept;

private:
    T *my_data = nullptr;
    std::size_t my_rank = 0;
    std::vector<std::size_t> my_shape;
    std::span<T> my_values;
};

}  // namespace altx
