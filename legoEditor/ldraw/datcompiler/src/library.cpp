#include "library.hpp"

#include <cctype>
#include <iostream>
#include <system_error>

namespace ldc {

static bool isTrimChar(char c) {
    return std::isspace(static_cast<unsigned char>(c)) || c == '"';
}

std::string cleanPath(std::string_view rel, bool lower) {
    size_t b = 0, e = rel.size();
    while (b < e && isTrimChar(rel[b])) ++b;
    while (e > b && isTrimChar(rel[e - 1])) --e;

    std::string out(rel.substr(b, e - b));
    for (char& c : out) {
        if (c == '\\') c = '/';
        else if (lower) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return out;
}

Library::Library(const fs::path& root) {
    std::error_code ec;
    root_ = fs::absolute(root, ec).lexically_normal();

    // Same priority as library.py: p, then parts, then models. First match wins.
    for (const char* sub : {"p", "parts", "models"}) {
        fs::path base = root_ / sub;
        if (fs::is_directory(base, ec)) indexDir(base);
    }
}

std::optional<fs::path> Library::findRoot(const fs::path& start) {
    std::error_code ec;
    fs::path dir = fs::absolute(start, ec).lexically_normal();
    if (!fs::is_directory(dir, ec)) dir = dir.parent_path();
    for (int i = 0; i < 5 && !dir.empty(); ++i) {
        if (fs::is_directory(dir / "p", ec) && fs::is_directory(dir / "parts", ec)) return dir;
        fs::path parent = dir.parent_path();
        if (parent == dir) break;
        dir = parent;
    }
    return std::nullopt;
}

void Library::setLocalDir(const fs::path& dir) {
    localDir_ = dir;
    resolveCache_.clear();
    texCache_.clear();
}

void Library::indexDir(const fs::path& base) {
    std::error_code ec;
    const std::string baseStr = base.generic_string();

    fs::recursive_directory_iterator it(base, fs::directory_options::skip_permission_denied, ec), end;
    for (; !ec && it != end; it.increment(ec)) {
        if (!it->is_regular_file(ec)) continue;
        const fs::path& full = it->path();
        std::string gen = full.generic_string();
        if (gen.size() <= baseStr.size() + 1) continue;
        index_.emplace(normalisePath(std::string_view(gen).substr(baseStr.size() + 1)), full);
    }
    if (ec) std::cerr << "LIBRARY: WARNING, error while indexing " << base << ": " << ec.message() << "\n";
}

std::optional<fs::path> Library::resolve(std::string_view relPath) const {
    const std::string key = normalisePath(relPath);
    if (auto it = resolveCache_.find(key); it != resolveCache_.end()) return it->second;

    std::optional<fs::path> found;
    std::error_code ec;
    if (!localDir_.empty()) {
        // As written first (case-sensitive file systems), then lowercased
        for (const fs::path& cand : {localDir_ / cleanPath(relPath, false), localDir_ / key}) {
            if (fs::is_regular_file(cand, ec)) { found = cand; break; }
        }
    }
    if (!found) {
        if (auto it = index_.find(key); it != index_.end()) found = it->second;
    }
    resolveCache_.emplace(key, found);
    return found;
}

std::optional<fs::path> Library::resolveTexture(std::string_view name, const fs::path& fileDir) const {
    const std::string clean = cleanPath(name, false);
    const std::string cacheKey = fileDir.generic_string() + "|" + clean;
    if (auto it = texCache_.find(cacheKey); it != texCache_.end()) return it->second;

    std::optional<fs::path> found;
    std::error_code ec;
    for (const std::string& rel : {"textures/" + clean, clean}) {
        if (!fileDir.empty()) {
            fs::path cand = fileDir / rel;
            if (fs::is_regular_file(cand, ec)) { found = cand; break; }
        }
        if (auto it = index_.find(normalisePath(rel)); it != index_.end()) { found = it->second; break; }
    }
    texCache_.emplace(cacheKey, found);
    return found;
}

std::string Library::storedPath(const fs::path& p) const {
    fs::path rel = p.lexically_normal().lexically_relative(root_);
    if (!rel.empty() && *rel.begin() != "..") return rel.generic_string();
    return p.generic_string();
}

} // namespace ldc
