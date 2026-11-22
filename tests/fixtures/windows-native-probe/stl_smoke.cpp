// Includes MSVC STL headers the same way the native release dependencies do.
#include <filesystem>
#include <string>
#include <vector>

int main() {
    std::vector<std::string> parts{"probe", "stl"};
    return std::filesystem::path(parts.front()).empty() ? 1 : 0;
}
