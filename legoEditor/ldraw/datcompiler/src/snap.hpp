#pragma once
// LDCad shadow library snap info (0 !LDCAD SNAP_* metas), ported from snapping.py.
//
// Resolution model, equivalent to parsePart + applyShadow:
//   - every file's flattened result carries RawSnaps: a snap definition from some
//     shadow file plus the accumulated matrix from that shadow file's part to here
//   - a file's own shadow metas run after its subfiles: SNAP_CLEAR filters only what
//     was inherited, then the file's own snaps are appended
//   - at the top-level part, each RawSnap is transformed once by its accumulated
//     matrix with the scale/mirror inheritance rules enforced, then deduped
// Enforcing at the top (rather than at each level, as applyShadow does) gives the
// same result, because applyShadow already uses the full top-level matrix and a
// dropped snap can't affect a later SNAP_CLEAR.

#include "geometry.hpp"
#include "library.hpp"

#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace ldc {

enum SnapKind : uint8_t { SNAP_CYL = 0, SNAP_CLP, SNAP_FGR, SNAP_GEN, SNAP_SPH };
// "R", "A", "S", "_L", "L_" for cylinders; "M"/"F" per finger for SNAP_FGR
enum SecShape : uint8_t { SEC_R = 0, SEC_A, SEC_S, SEC_UL, SEC_LU, SEC_M, SEC_F };

const char* snapKindName(SnapKind k);  // "CYL", "CLP", ...
const char* secShapeName(SecShape s);  // "R", "_L", "M", ...

struct Sec {
    SecShape shape;
    double radius, length;
};

// General affine 3x4 (rotation and possibly scale, plus translation)
struct Affine {
    double r[3][3];
    double t[3];

    static Affine identity() { return {{{1, 0, 0}, {0, 1, 0}, {0, 0, 1}}, {0, 0, 0}}; }
    static Affine from(const Mat34& m) {
        Affine a;
        for (int i = 0; i < 3; ++i) {
            for (int j = 0; j < 3; ++j) a.r[i][j] = m.r[i][j];
            a.t[i] = m.t[i];
        }
        return a;
    }
    Affine operator*(const Affine& b) const {
        Affine o;
        for (int i = 0; i < 3; ++i) {
            for (int j = 0; j < 3; ++j)
                o.r[i][j] = r[i][0] * b.r[0][j] + r[i][1] * b.r[1][j] + r[i][2] * b.r[2][j];
            o.t[i] = r[i][0] * b.t[0] + r[i][1] * b.t[1] + r[i][2] * b.t[2] + t[i];
        }
        return o;
    }
};

struct Snap {
    SnapKind kind = SNAP_CYL;
    char gender = 'M';                 // 'M' or 'F' (fingers: gender of the first finger)
    std::string group;
    std::vector<std::string> ids;      // lowercased, sorted, unique
    std::vector<Sec> secs;
    bool capA = false, capB = false, center = false, slide = false;
    double radius = 0.0, length = 0.0;
    std::vector<std::string> bounding; // GEN bounding tokens
    std::string scaleRule = "none";    // none / yonly / ronly / yandr
    std::string mirrorRule = "none";   // none / cor
    Affine matrix = Affine::identity();
};
using SnapPtr = std::shared_ptr<const Snap>;

// A snap definition placed by an accumulated (not yet enforced) matrix
struct RawSnap {
    SnapPtr def;
    Affine acc;
};

struct ShadowRecord {
    bool isClear;
    std::string clearId;  // "" = clear everything inherited
    SnapPtr snap;
};
using ShadowRecords = std::vector<ShadowRecord>;

struct ShadowStats {
    size_t filesRead = 0;
    size_t missingIncludes = 0;
    size_t badSnaps = 0;
};

class ShadowLibrary {
public:
    ShadowLibrary(const fs::path& root, const Library& lib);

    bool valid() const { return !dirs_.empty(); }
    const fs::path& root() const { return root_; }
    size_t size() const;

    // Records of the shadow file mirroring a resolved LDraw part file, or nullptr
    const ShadowRecords* recordsFor(const fs::path& partFile);

    const ShadowStats& stats() const { return stats_; }

private:
    struct Dir {
        std::string folder;  // "parts" or "p"
        std::unordered_map<std::string, fs::path> files; // lowercase rel -> path
    };

    std::optional<fs::path> resolveShadowPart(const fs::path& file) const;
    std::optional<fs::path> resolveShadowRef(std::string_view ref) const;
    const ShadowRecords& readShadow(const fs::path& path, bool followIncludes);
    void include(const std::unordered_map<std::string, std::string>& p, const fs::path& owner,
                 ShadowRecords& out);

    fs::path root_;
    const Library& lib_;
    std::vector<Dir> dirs_;  // search order, as getShadowSearchDirs
    std::unordered_map<std::string, ShadowRecords> cache_;  // path + flag
    std::unordered_map<std::string, std::optional<fs::path>> partCache_;
    ShadowStats stats_;
};

// _transform: move a snap by M. With enforce, its scale/mirror rules may drop it.
std::optional<Snap> transformSnap(const Snap& s, const Affine& M, bool enforce);

// Top-level resolution: transform every RawSnap (rules enforced), then dedupe
std::vector<Snap> resolveSnaps(const std::vector<RawSnap>& raw);

} // namespace ldc
