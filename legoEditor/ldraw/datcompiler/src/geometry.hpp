#pragma once
#include <cmath>
#include <cstdint>
#include <utility>

namespace ldc {

struct Vec3 {
    double x, y, z;
};

inline Vec3 operator+(Vec3 a, Vec3 b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
inline Vec3 operator-(Vec3 a, Vec3 b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
inline Vec3 operator*(Vec3 a, double s) { return {a.x * s, a.y * s, a.z * s}; }
inline double dot(Vec3 a, Vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline Vec3 cross(Vec3 a, Vec3 b) { return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
inline double length(Vec3 a) { return std::sqrt(dot(a, a)); }

// LDraw type 1 transform: [a b c x; d e f y; g h i z]
struct Mat34 {
    double r[3][3];
    double t[3];

    double det() const {
        return r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
             - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
             + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0]);
    }

    Vec3 apply(const Vec3& p) const {
        return {
            r[0][0] * p.x + r[0][1] * p.y + r[0][2] * p.z + t[0],
            r[1][0] * p.x + r[1][1] * p.y + r[1][2] * p.z + t[1],
            r[2][0] * p.x + r[2][1] * p.y + r[2][2] * p.z + t[2],
        };
    }
};

// Per-face flags. The low bits are written to the blob as-is.
enum : uint8_t {
    FACE_DIRECT_COLOUR = 1u << 0, // colour holds 0xRRGGBB, not an LDraw colour code
    FACE_TEXTURED      = 1u << 1, // tex and uv are valid for this face
    FACE_TEX_RESOLVED  = 1u << 7, // internal only: a TEXMAP already decided this face
};
constexpr uint8_t FACE_FLAGS_WRITTEN = FACE_DIRECT_COLOUR | FACE_TEXTURED;

struct Colour {
    int32_t value;  // LDraw code, or 0xRRGGBB if direct
    bool direct;
};

// One flattened triangle or quad. Positions are float: enough for LDU precision,
// and it keeps the cache small. All transform maths is done in double.
struct Face {
    float p[4][3];
    float uv[4][2];  // LDraw texture convention (v = 0 at the top of the image)
    int32_t colour;
    int32_t tex;     // index into Flattener::textures(), -1 = none
    uint8_t n;       // 3 or 4
    uint8_t flags;

    Vec3 pt(int k) const { return {p[k][0], p[k][1], p[k][2]}; }
    void setPt(int k, const Vec3& v) {
        p[k][0] = static_cast<float>(v.x);
        p[k][1] = static_cast<float>(v.y);
        p[k][2] = static_cast<float>(v.z);
    }
    void reverse() {
        for (int a = 0, b = n - 1; a < b; ++a, --b) {
            std::swap(p[a], p[b]);
            std::swap(uv[a], uv[b]);
        }
    }
};

} // namespace ldc
