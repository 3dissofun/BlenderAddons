#pragma once
// !TEXMAP projections, ported from textures.py (planar, cylindrical, spherical).

#include "geometry.hpp"

namespace ldc {

struct TexMap {
    enum Type { PLANAR, CYLINDRICAL, SPHERICAL } type;
    double prm[11];   // P1, P2, P3 (in the space of the file that declared it), then angles in degrees
    int32_t tex;      // index into Flattener::textures()
};

// UVs in LDraw convention (v = 0 at the top of the image).
// Returns false if the projection is degenerate; the face is then left untextured.
bool computeUV(const TexMap& t, const Vec3* pts, int n, float (*uvOut)[2]);

} // namespace ldc
