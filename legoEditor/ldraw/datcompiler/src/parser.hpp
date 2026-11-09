#pragma once
// Flattens an LDraw file: every type 1 reference is expanded recursively until
// only triangles and quads remain.
//
// Conventions of the flattened output (in the file's own local space):
//   - every face is wound counter-clockwise (BFC CW, INVERTNEXT and mirrored
//     matrices are all resolved)
//   - colour 16 is kept as 16 so the result can be placed with any colour later
//   - direct colours (0x2RRGGBB) are stored as 0xRRGGBB with FACE_DIRECT_COLOUR
//   - TEXMAP UVs are baked per face vertex
//
// Each file is flattened once and kept in an LRU cache, so a primitive like
// stud.dat is parsed once per run however many parts reference it.
//
// TEXMAP and caching: a texmap active in a parent also applies to a child's
// untextured faces. The cached child stays texture-free; the parent applies its
// texmap after transforming the child's faces into its own space, skipping faces
// a deeper texmap already decided (FACE_TEX_RESOLVED).

#include "geometry.hpp"
#include "library.hpp"
#include "texmap.hpp"

#include <list>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace ldc {

using FaceList = std::vector<Face>;

// A type 2 edge line. Kept so the blob can mark the matching mesh edges sharp.
struct Edge {
    float p[2][3];
};
using EdgeList = std::vector<Edge>;

struct Geometry {
    FaceList faces;
    EdgeList edges;
};

struct TextureEntry {
    std::string tex;    // stored path: relative to the library root when inside it
    std::string gloss;  // GLOSSMAP texture, empty if none
};

struct FlattenStats {
    size_t filesParsed = 0;
    size_t cacheHits = 0;
    size_t cacheEvictions = 0;
    size_t missingRefs = 0;
    size_t missingTextures = 0;
    size_t badLines = 0;
};

class Flattener {
public:
    Flattener(const Library& lib, size_t cacheBudgetBytes);

    // Fully flattened faces and edge lines of a file in its local space.
    std::shared_ptr<const Geometry> flatten(const fs::path& file);

    // Global texture table; Face::tex indexes into this.
    const std::vector<TextureEntry>& textures() const { return textures_; }

    const FlattenStats& stats() const { return stats_; }

private:
    void parseInto(const fs::path& file, Geometry& out);
    std::optional<TexMap> parseTexmap(const std::vector<std::string_view>& args, size_t start,
                                      const fs::path& file, size_t lineNo);
    int32_t registerTexture(const std::string& tex, const std::string& gloss);

    std::shared_ptr<const Geometry> cacheGet(const std::string& key);
    void cachePut(const std::string& key, std::shared_ptr<const Geometry> geom);

    const Library& lib_;

    struct CacheEntry {
        std::shared_ptr<const Geometry> geom;
        std::list<std::string>::iterator lruIt;
        size_t bytes;
    };
    std::unordered_map<std::string, CacheEntry> cache_;
    std::list<std::string> lru_;   // front = most recently used
    size_t cacheBytes_ = 0;
    size_t cacheBudget_;

    std::unordered_set<std::string> inProgress_;   // cycle guard
    std::unordered_set<std::string> warnedMissing_;

    std::vector<TextureEntry> textures_;
    std::unordered_map<std::string, int32_t> textureIndex_;

    FlattenStats stats_;
};

} // namespace ldc
