/// @file
/// @brief The owning, row-major, aligned array of `core/Tensor.hpp`.
///
/// The plan asks for a small owning class parameterised on an allocator
/// policy, not a wrapper around `std::vector`: device memory cannot live in a
/// vector, and host memory must be aligned for BLAS and SIMD
/// (DevelopmentPlan.md, "Storing tensors"). What is pinned here is what the
/// rest of the program will rely on:
///
///   * the elements are one contiguous, **row-major** block -- the last index
///     varies fastest -- so `(m, l, n_laws)` gives every channel a contiguous
///     `l x n_laws` matrix that BLAS can take directly (rule 2 of "The ALTX
///     file");
///   * `HostAlloc` memory is 64-byte aligned, whatever the element type and
///     however few elements there are;
///   * a new tensor is all zeros, never whatever the allocator returned --
///     which may be a NaN, and NaN is not a legal value;
///   * a tensor owns its memory alone: it cannot be copied, moving it is
///     `noexcept` and allocates nothing, and the moved-from tensor is empty;
///   * all memory goes through the allocator policy, once in and once out, so
///     a `HipAlloc` can later be dropped in without touching `Tensor`;
///   * `operator()` is the unchecked, `noexcept` access for kernels; `at()` is
///     the checked one, and throws an `altx::Error`.

#include <gtest/gtest.h>

#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <span>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#include "core/Exceptions.hpp"
#include "core/Tensor.hpp"

namespace
{

// --- Compile-time properties ------------------------------------------------

using DTensor = altx::Tensor<double>;
using FTensor = altx::Tensor<float>;

// HostAlloc is the default policy, so `Tensor<Real>` means host memory.
static_assert(std::is_same_v<DTensor, altx::Tensor<double, altx::HostAlloc>>);
static_assert(std::is_same_v<DTensor::value_type, double>);
static_assert(std::is_same_v<DTensor::allocator_type, altx::HostAlloc>);

/// @brief Whether `altx::Tensor<T>` is a valid type at all.
///
/// A constraint on a class template makes a specialisation that fails it
/// ill-formed when it is named, which a requires-expression can observe.
template <typename T>
concept NamesATensor = requires { typename altx::Tensor<T>; };

/// @brief A struct that is copyable as bytes, but not arithmetic.
struct PlainPair
{
    /// @brief First member.
    double first;
    /// @brief Second member.
    double second;
};

// Elements are built-in numbers: the scalar types, and the integer types the
// file holds (`P_classes`, `lengths`).
static_assert(NamesATensor<double>);
static_assert(NamesATensor<float>);
static_assert(NamesATensor<int>);
static_assert(NamesATensor<std::int32_t>);
static_assert(NamesATensor<std::size_t>);

// A tensor never runs constructors or destructors, so a type that has
// meaningful ones must not compile.
static_assert(!NamesATensor<std::string>);
static_assert(!NamesATensor<std::vector<double>>);
// Narrower still, by choice: not even a struct of numbers. MPI and HDF5 would
// need a hand-built datatype for it.
static_assert(!NamesATensor<PlainPair>);

// 64 bytes is a cache line and the widest SIMD register (AVX-512).
static_assert(altx::HostAlloc::alignment == 64);

// A tensor may hold gigabytes, and a device one cannot be copied with
// memcpy: an accidental copy must not compile.
static_assert(!std::is_copy_constructible_v<DTensor>);
static_assert(!std::is_copy_assignable_v<DTensor>);

// Moving hands the buffer over; it must not throw, or a std::vector<Tensor>
// would fall back to copying -- which does not exist.
static_assert(std::is_nothrow_move_constructible_v<DTensor>);
static_assert(std::is_nothrow_move_assignable_v<DTensor>);
static_assert(std::is_nothrow_destructible_v<DTensor>);
static_assert(std::is_nothrow_default_constructible_v<DTensor>);

// A shape is not a tensor: `Tensor t = {2, 3};` must not compile.
static_assert(
    !std::is_convertible_v<std::initializer_list<std::size_t>, DTensor>);
static_assert(!std::is_convertible_v<std::span<const std::size_t>, DTensor>);
static_assert(std::is_constructible_v<DTensor, std::span<const std::size_t>>);

// The kernel-side interface cannot throw: HIP device code has no exceptions,
// and the CPU and GPU kernels share one structure.
static_assert(noexcept(std::declval<DTensor &>()(0, 0)));
static_assert(noexcept(std::declval<const DTensor &>()(0, 0)));
static_assert(noexcept(std::declval<DTensor &>().data()));
static_assert(noexcept(std::declval<DTensor &>().values()));
static_assert(noexcept(std::declval<const DTensor &>().size()));
static_assert(noexcept(std::declval<const DTensor &>().rank()));
static_assert(noexcept(std::declval<const DTensor &>().shape()));
static_assert(noexcept(std::declval<const DTensor &>().empty()));
static_assert(noexcept(std::declval<DTensor &>().fill(1.0)));

// Const in, const out: a const tensor hands out nothing writable.
static_assert(
    std::is_same_v<decltype(std::declval<DTensor &>()(0, 0)), double &>);
static_assert(std::is_same_v<decltype(std::declval<const DTensor &>()(0, 0)),
                             const double &>);
static_assert(std::is_same_v<decltype(std::declval<const DTensor &>().at(0, 0)),
                             const double &>);
static_assert(
    std::is_same_v<decltype(std::declval<DTensor &>().data()), double *>);
static_assert(std::is_same_v<decltype(std::declval<const DTensor &>().data()),
                             const double *>);
static_assert(std::is_same_v<decltype(std::declval<DTensor &>().values()),
                             std::span<double>>);
static_assert(std::is_same_v<decltype(std::declval<const DTensor &>().values()),
                             std::span<const double>>);
static_assert(std::is_same_v<decltype(std::declval<const DTensor &>().shape()),
                             std::span<const std::size_t>>);

/// @brief Whether @p pointer is a multiple of @p alignment.
/// @param pointer The address to check.
/// @param alignment A power of two, in bytes.
/// @return `true` if the address is aligned.
bool isAligned(const void *pointer, std::size_t alignment)
{
    return reinterpret_cast<std::uintptr_t>(pointer) % alignment == 0;
}

/// @brief An allocator policy that counts its calls, on top of HostAlloc.
///
/// Static, because a policy is a type, not an object: Tensor never holds an
/// instance of it.
struct CountingAlloc
{
    /// @brief Number of calls to allocate().
    static inline int allocations = 0;
    /// @brief Number of calls to deallocate().
    static inline int deallocations = 0;
    /// @brief The byte count of the last allocate() call.
    static inline std::size_t lastBytes = 0;

    /// @brief Resets every counter.
    static void reset()
    {
        allocations = 0;
        deallocations = 0;
        lastBytes = 0;
    }

    /// @brief Counts, then forwards to HostAlloc::allocate().
    /// @param bytes The size of the block.
    /// @return The block.
    static void *allocate(std::size_t bytes)
    {
        ++allocations;
        lastBytes = bytes;
        return altx::HostAlloc::allocate(bytes);
    }

    /// @brief Counts, then forwards to HostAlloc::deallocate().
    /// @param pointer The block, as allocate() returned it.
    /// @param bytes The size it was allocated with.
    static void deallocate(void *pointer, std::size_t bytes) noexcept
    {
        ++deallocations;
        altx::HostAlloc::deallocate(pointer, bytes);
    }
};

// --- The allocator policy ---------------------------------------------------

/// @brief HostAlloc returns 64-byte aligned, writable memory, including for
/// sizes that are not a multiple of the alignment.
TEST(HostAlloc, ReturnsAlignedMemory)
{
    for (const std::size_t bytes : {1U, 8U, 63U, 64U, 65U, 4096U, 100000U})
    {
        void *block = altx::HostAlloc::allocate(bytes);
        ASSERT_NE(block, nullptr) << bytes;
        EXPECT_TRUE(isAligned(block, altx::HostAlloc::alignment)) << bytes;
        static_cast<unsigned char *>(block)[bytes - 1] = 0xFF;
        altx::HostAlloc::deallocate(block, bytes);
    }
}

/// @brief Tensor allocates once on construction, through its policy, for
/// exactly `size() * sizeof(T)` bytes, and gives the block back once.
TEST(Tensor, UsesItsAllocatorPolicyOnce)
{
    CountingAlloc::reset();
    {
        const altx::Tensor<float, CountingAlloc> tensor{2, 3, 5};
        EXPECT_EQ(CountingAlloc::allocations, 1);
        EXPECT_EQ(CountingAlloc::lastBytes, 2 * 3 * 5 * sizeof(float));
        EXPECT_EQ(CountingAlloc::deallocations, 0);
    }
    EXPECT_EQ(CountingAlloc::deallocations, 1);
}

// --- Shape ------------------------------------------------------------------

/// @brief A default-constructed tensor is empty: no rank, no elements.
TEST(Tensor, DefaultIsEmpty)
{
    const DTensor tensor;
    EXPECT_TRUE(tensor.empty());
    EXPECT_EQ(tensor.size(), std::size_t{0});
    EXPECT_EQ(tensor.rank(), std::size_t{0});
    EXPECT_TRUE(tensor.shape().empty());
    EXPECT_TRUE(tensor.values().empty());
}

/// @brief The shape is reported as given, and the size is its product.
TEST(Tensor, ReportsShapeAndSize)
{
    const DTensor tensor{2, 3, 4};
    EXPECT_FALSE(tensor.empty());
    EXPECT_EQ(tensor.rank(), std::size_t{3});
    EXPECT_EQ(tensor.size(), std::size_t{24});
    ASSERT_EQ(tensor.shape().size(), std::size_t{3});
    EXPECT_EQ(tensor.shape()[0], std::size_t{2});
    EXPECT_EQ(tensor.shape()[1], std::size_t{3});
    EXPECT_EQ(tensor.shape()[2], std::size_t{4});
    EXPECT_EQ(tensor.values().size(), std::size_t{24});
}

/// @brief A shape known only at run time -- one read from a file -- is
/// accepted as a span, and the tensor keeps its own copy of it.
TEST(Tensor, ConstructsFromARuntimeShape)
{
    std::vector<std::size_t> shape = {5, 1, 7, 2};
    const DTensor tensor{std::span<const std::size_t>(shape)};
    shape.assign({9, 9, 9, 9});

    ASSERT_EQ(tensor.rank(), std::size_t{4});
    EXPECT_EQ(tensor.shape()[0], std::size_t{5});
    EXPECT_EQ(tensor.shape()[1], std::size_t{1});
    EXPECT_EQ(tensor.shape()[2], std::size_t{7});
    EXPECT_EQ(tensor.shape()[3], std::size_t{2});
    EXPECT_EQ(tensor.size(), std::size_t{70});
}

/// @brief An extent of zero is legal -- a class may yield no laws -- and
/// gives a tensor with a rank but no elements.
TEST(Tensor, ZeroExtentGivesNoElements)
{
    const DTensor tensor{3, 0, 4};
    EXPECT_EQ(tensor.rank(), std::size_t{3});
    EXPECT_EQ(tensor.size(), std::size_t{0});
    EXPECT_TRUE(tensor.empty());
    EXPECT_EQ(tensor.shape()[1], std::size_t{0});
}

/// @brief An empty shape is rejected: a rank-0 tensor would be a scalar,
/// which nothing in the algorithm needs, and would be confused with "empty".
TEST(Tensor, RejectsAnEmptyShape)
{
    const std::vector<std::size_t> none;
    EXPECT_THROW(DTensor{std::span<const std::size_t>(none)}, altx::Error);
}

/// @brief A shape whose element count, or byte count, does not fit in a
/// `std::size_t` is rejected rather than wrapped around to a small one.
TEST(Tensor, RejectsAShapeThatOverflows)
{
    constexpr std::size_t kMax = std::numeric_limits<std::size_t>::max();
    EXPECT_THROW((DTensor{kMax, 2}), altx::Error);
    EXPECT_THROW((DTensor{std::size_t{1} << 32, std::size_t{1} << 32}),
                 altx::Error);
    // The element count fits; the byte count does not.
    EXPECT_THROW((DTensor{kMax / 2}), altx::Error);
    // 2^62 doubles are 2^65 bytes, which wraps around to exactly zero: a
    // check on the wrapped value accepts it and allocates nothing.
    EXPECT_THROW((DTensor{std::size_t{1} << 62}), altx::Error);
    EXPECT_THROW((FTensor{std::size_t{1} << 31, std::size_t{1} << 31}),
                 altx::Error);
}

// --- Storage ----------------------------------------------------------------

/// @brief A new tensor holds zeros, not whatever the allocator returned.
TEST(Tensor, StartsZeroed)
{
    const DTensor tensor{7, 11};
    for (const double value : tensor.values())
    {
        EXPECT_EQ(value, 0.0);
    }
}

/// @brief The data is 64-byte aligned for both scalar types and for awkward
/// sizes, so every row of a BLAS operand starts on a cache line boundary.
TEST(Tensor, DataIsAligned)
{
    for (const std::size_t n : {1U, 3U, 17U, 1000U})
    {
        const DTensor doubles{n};
        const FTensor floats{n, 3};
        EXPECT_TRUE(isAligned(doubles.data(), 64)) << n;
        EXPECT_TRUE(isAligned(floats.data(), 64)) << n;
    }
}

/// @brief `values()` spans exactly the elements, starting at `data()`.
TEST(Tensor, ValuesSpansTheData)
{
    DTensor tensor{4, 5};
    const std::span<double> values = tensor.values();
    EXPECT_EQ(values.data(), tensor.data());
    EXPECT_EQ(values.size(), tensor.size());

    const DTensor &view = tensor;
    EXPECT_EQ(view.values().data(), view.data());
}

/// @brief `fill()` sets every element.
TEST(Tensor, FillSetsEveryElement)
{
    FTensor tensor{3, 3, 3};
    tensor.fill(2.5F);
    for (const float value : tensor.values())
    {
        EXPECT_EQ(value, 2.5F);
    }
}

/// @brief Two tensors never share storage.
TEST(Tensor, TensorsAreIndependent)
{
    DTensor first{2, 2};
    DTensor second{2, 2};
    EXPECT_NE(first.data(), second.data());
    first.fill(1.0);
    for (const double value : second.values())
    {
        EXPECT_EQ(value, 0.0);
    }
}

// --- Row-major indexing -----------------------------------------------------

/// @brief Rank 1: index `i` is element `i`.
TEST(Tensor, IndexesRankOne)
{
    DTensor tensor{6};
    for (std::size_t i = 0; i < 6; ++i)
    {
        EXPECT_EQ(&tensor(i), tensor.data() + i) << i;
    }
}

/// @brief Rank 2: `(i, j)` of an `R x C` tensor is element `i * C + j`; the
/// last index varies fastest.
TEST(Tensor, IndexesRankTwoRowMajor)
{
    constexpr std::size_t kRows = 3;
    constexpr std::size_t kCols = 5;
    DTensor tensor{kRows, kCols};
    for (std::size_t i = 0; i < kRows; ++i)
    {
        for (std::size_t j = 0; j < kCols; ++j)
        {
            EXPECT_EQ(&tensor(i, j), tensor.data() + i * kCols + j)
                << i << ", " << j;
        }
    }
}

/// @brief Writing through `operator()` in row-major order fills the flat
/// buffer in order -- the layout a file reader or BLAS sees.
TEST(Tensor, RowMajorWritesFillTheBufferInOrder)
{
    DTensor tensor{2, 3, 4};
    double next = 0.0;
    for (std::size_t a = 0; a < 2; ++a)
    {
        for (std::size_t b = 0; b < 3; ++b)
        {
            for (std::size_t c = 0; c < 4; ++c)
            {
                tensor(a, b, c) = next;
                next += 1.0;
            }
        }
    }
    const std::span<const double> values = std::as_const(tensor).values();
    for (std::size_t k = 0; k < values.size(); ++k)
    {
        EXPECT_EQ(values[k], static_cast<double>(k)) << k;
    }
}

/// @brief The `(m, l, n_laws)` layout of `P`: each channel's laws are one
/// contiguous `l x n_laws` row-major matrix with leading dimension `n_laws`,
/// the channels one after another -- what `gemm` takes without a copy.
TEST(Tensor, ChannelOutermostLayoutOfP)
{
    constexpr std::size_t kChannels = 3;  // m
    constexpr std::size_t kL = 4;         // l
    constexpr std::size_t kLaws = 5;      // n_laws
    DTensor laws{kChannels, kL, kLaws};

    for (std::size_t channel = 0; channel < kChannels; ++channel)
    {
        const double *block = &laws(channel, 0, 0);
        EXPECT_EQ(block, laws.data() + channel * kL * kLaws) << channel;
        for (std::size_t row = 0; row < kL; ++row)
        {
            for (std::size_t law = 0; law < kLaws; ++law)
            {
                EXPECT_EQ(&laws(channel, row, law), block + row * kLaws + law)
                    << channel << ", " << row << ", " << law;
            }
        }
    }
}

/// @brief Rank 4 follows the same rule, and an extent of one changes nothing.
TEST(Tensor, IndexesRankFour)
{
    DTensor tensor{2, 1, 3, 2};
    EXPECT_EQ(&tensor(1, 0, 2, 1),
              tensor.data() + ((1 * 1 + 0) * 3 + 2) * 2 + 1);
    EXPECT_EQ(&tensor(0, 0, 1, 0), tensor.data() + 2);
}

/// @brief Indices of any integral type work, so a loop counter does not need
/// a cast; the const overload reads what the other one wrote.
TEST(Tensor, AcceptsAnyIntegralIndex)
{
    FTensor tensor{3, 4};
    const int row = 2;
    const unsigned col = 3U;
    tensor(row, col) = 7.0F;
    const FTensor &view = tensor;
    EXPECT_EQ(view(std::size_t{2}, std::int64_t{3}), 7.0F);
    EXPECT_EQ(&view(row, col), view.data() + 11);
}

// --- Checked access ---------------------------------------------------------

/// @brief `at()` reaches the same element as `operator()`.
TEST(Tensor, AtMatchesTheUncheckedAccess)
{
    DTensor tensor{3, 4, 2};
    EXPECT_EQ(&tensor.at(2, 1, 1), &tensor(2, 1, 1));
    tensor.at(0, 3, 0) = 4.0;
    EXPECT_EQ(std::as_const(tensor).at(0, 3, 0), 4.0);
}

/// @brief `at()` rejects an index past the end of its dimension -- in any
/// dimension, including one that would still land inside the buffer.
TEST(Tensor, AtRejectsAnIndexOutOfRange)
{
    DTensor tensor{3, 4};
    EXPECT_THROW(tensor.at(3, 0), altx::Error);
    EXPECT_THROW(tensor.at(0, 4), altx::Error);  // offset 4 is in the buffer
    EXPECT_THROW(tensor.at(-1, 0), altx::Error);
    EXPECT_NO_THROW(tensor.at(2, 3));
}

/// @brief `at()` rejects a number of indices different from the rank.
TEST(Tensor, AtRejectsTheWrongNumberOfIndices)
{
    DTensor tensor{3, 4};
    EXPECT_THROW(tensor.at(1), altx::Error);
    EXPECT_THROW(tensor.at(1, 1, 0), altx::Error);
    EXPECT_THROW(std::as_const(tensor).at(1, 1, 0), altx::Error);
}

/// @brief An empty tensor has nothing to reach.
TEST(Tensor, AtOnAnEmptyTensorThrows)
{
    DTensor none;
    EXPECT_THROW(none.at(0), altx::Error);
    // No indices matches the rank of an empty tensor, but still reaches no
    // element.
    EXPECT_THROW(none.at(), altx::Error);
    EXPECT_THROW(std::as_const(none).at(), altx::Error);
    DTensor zero{2, 0};
    EXPECT_THROW(zero.at(0, 0), altx::Error);
}

// --- Ownership --------------------------------------------------------------

/// @brief Moving hands over the buffer itself: same address, same shape, same
/// values, and the source is left empty.
TEST(Tensor, MoveConstructionTransfersTheBuffer)
{
    DTensor source{2, 3};
    source(1, 2) = 5.0;
    const double *buffer = source.data();

    const DTensor target(std::move(source));
    EXPECT_EQ(target.data(), buffer);
    EXPECT_EQ(target.rank(), std::size_t{2});
    EXPECT_EQ(target.size(), std::size_t{6});
    EXPECT_EQ(target(1, 2), 5.0);

    // NOLINTNEXTLINE(bugprone-use-after-move): the moved-from state is pinned.
    EXPECT_TRUE(source.empty());
    EXPECT_EQ(source.rank(), std::size_t{0});
    EXPECT_EQ(source.size(), std::size_t{0});
}

/// @brief Move assignment releases the target's old buffer, takes the
/// source's, and leaves the source empty.
TEST(Tensor, MoveAssignmentReleasesTheOldBuffer)
{
    CountingAlloc::reset();
    {
        altx::Tensor<double, CountingAlloc> target{4};
        altx::Tensor<double, CountingAlloc> source{2, 2};
        const double *buffer = source.data();
        ASSERT_EQ(CountingAlloc::allocations, 2);

        target = std::move(source);
        EXPECT_EQ(CountingAlloc::allocations, 2);  // a move allocates nothing
        EXPECT_EQ(CountingAlloc::deallocations, 1);
        EXPECT_EQ(target.data(), buffer);
        EXPECT_EQ(target.rank(), std::size_t{2});
        // NOLINTNEXTLINE(bugprone-use-after-move): pinned on purpose.
        EXPECT_TRUE(source.empty());
    }
    // Two buffers in, two out: none leaked, none freed twice.
    EXPECT_EQ(CountingAlloc::deallocations, 2);
}

/// @brief A moved-from tensor is a valid empty one: it can be assigned again.
TEST(Tensor, MovedFromTensorIsReusable)
{
    DTensor source{3};
    const DTensor taken(std::move(source));
    source = DTensor{2, 5};
    EXPECT_EQ(source.size(), std::size_t{10});
    EXPECT_EQ(taken.size(), std::size_t{3});
}

/// @brief Tensors can live in a growing std::vector, which relocates them by
/// move -- the per-triplet `P` will be kept that way.
TEST(Tensor, LivesInAVector)
{
    std::vector<DTensor> tensors;
    for (std::size_t i = 1; i <= 20; ++i)
    {
        tensors.emplace_back(std::initializer_list<std::size_t>{i, 2});
        tensors.back().fill(static_cast<double>(i));
    }
    for (std::size_t i = 0; i < tensors.size(); ++i)
    {
        EXPECT_EQ(tensors[i].shape()[0], i + 1);
        EXPECT_EQ(tensors[i](i, 1), static_cast<double>(i + 1));
    }
}

}  // namespace
