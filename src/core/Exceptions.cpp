#include <stdexcept>
namespace altx
{
class Error : public std::runtime_error
{
public:
    Error(std::string what_is_wrong) : std::runtime_error("") {}
};
}  // namespace altx
