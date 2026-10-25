#pragma once

#include <cmath>
#include <concepts>
#include <cstddef>
#include <cstdlib>
#include <initializer_list>
#include <span>

namespace altx
{

struct HostAlloc
{
    static constexpr std::size_t alignment = 64;
    static void *allocate(std::size_t bytes);  // throws std::bad_alloc
    static void deallocate(void *pointer, std::size_t bytes) noexcept;
};
template <typename T, typename Alloc = HostAlloc>
class Tensor
{
public:
    using value_type = T;
    using allocator_type = Alloc;
    Tensor() noexcept;
    Tensor(const Tensor &) = delete;
    Tensor &operator=(const Tensor &) = delete;
    Tensor(Tensor &&) noexcept;
    Tensor &operator=(Tensor &&) noexcept;
    explicit Tensor(std::initializer_list<std::size_t> shape);
    explicit Tensor(std::span<const std::size_t> shape);
    ~Tensor() {}
    std::size_t rank() const noexcept {}
    std::span<const std::size_t> shape() const noexcept {}
    std::size_t size() const noexcept;
    bool empty() const noexcept {}
    T *data() noexcept;
    const T *data() const noexcept;
    std::span<T> values() noexcept;
    std::span<const T> values() const;
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
};

}  // namespace altx
