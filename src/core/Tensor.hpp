#pragma once

#include <cmath>
#include <cstddef>
#include <cstdlib>
#include <initializer_list>

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
    explicit Tensor(std::initializer_list<std::size_t> shape);
};

}  // namespace altx
