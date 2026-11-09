#pragma once
// Resolves LDraw sub-file and texture references to real files on disk,
// case-insensitively, the same way library.py does.

#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>

namespace ldc {

namespace fs = std::filesystem;

// Trim whitespace/quotes, backslashes -> forward slashes, optionally lowercase.
std::string cleanPath(std::string_view rel, bool lower);
inline std::string normalisePath(std::string_view rel) { return cleanPath(rel, true); }

class Library {
public:
    // root is the folder containing p/, parts/ and models/
    explicit Library(const fs::path& root);

    // Walks up from start looking for a folder that has both p/ and parts/
    static std::optional<fs::path> findRoot(const fs::path& start);

    const fs::path& root() const { return root_; }
    size_t size() const { return index_.size(); }

    // Folder searched before the library, so a model's own local sub-files win.
    void setLocalDir(const fs::path& dir);

    std::optional<fs::path> resolve(std::string_view relPath) const;

    // Spec search: "textures/<name>" in the file's folder then the library,
    // then the same again without the prefix.
    std::optional<fs::path> resolveTexture(std::string_view name, const fs::path& fileDir) const;

    // Path relative to the library root (forward slashes) when inside it, else absolute.
    std::string storedPath(const fs::path& p) const;

private:
    void indexDir(const fs::path& base);

    fs::path root_;
    fs::path localDir_;
    std::unordered_map<std::string, fs::path> index_; // lowercase rel path -> absolute path

    // Lookups repeat constantly (every stud.dat reference), so cache them
    mutable std::unordered_map<std::string, std::optional<fs::path>> resolveCache_;
    mutable std::unordered_map<std::string, std::optional<fs::path>> texCache_;
};

} // namespace ldc
