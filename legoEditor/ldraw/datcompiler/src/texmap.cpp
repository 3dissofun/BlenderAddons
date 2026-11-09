#include "texmap.hpp"

#include <algorithm>

namespace ldc {

namespace {

constexpr double PI = 3.14159265358979323846;

// Stop a face that crosses the +-180 degree line from smearing the whole texture across it.
void fixSeam(double* u, int n, double angleRange) {
    const double turn = 2.0 * PI / angleRange; // one full revolution, in u units
    const double mx = *std::max_element(u, u + n);
    const double mn = *std::min_element(u, u + n);
    if (mx - mn > turn / 2.0) {
        const double mid = (mx + mn) / 2.0;
        for (int k = 0; k < n; ++k)
            if (u[k] < mid) u[k] += turn;
    }
}

Vec3 P(const double* prm, int i) { return {prm[3 * i], prm[3 * i + 1], prm[3 * i + 2]}; }

} // namespace

bool computeUV(const TexMap& t, const Vec3* pts, int n, float (*uvOut)[2]) {
    const Vec3 p1 = P(t.prm, 0), p2 = P(t.prm, 1), p3 = P(t.prm, 2);
    double u[4], v[4];

    switch (t.type) {
    case TexMap::PLANAR: {
        const Vec3 uAxis = p2 - p1, vAxis = p3 - p1;
        const double uu = dot(uAxis, uAxis), vv = dot(vAxis, vAxis);
        if (uu == 0.0 || vv == 0.0) return false;
        for (int k = 0; k < n; ++k) {
            const Vec3 d = pts[k] - p1;
            u[k] = dot(d, uAxis) / uu;
            v[k] = dot(d, vAxis) / vv;
        }
        break;
    }

    case TexMap::CYLINDRICAL: {
        // P1 bottom centre, P2 top centre, P3 on the bottom edge where the texture's
        // bottom-centre touches, a = angular extent mapped from -a/2 to +a/2.
        const double a = t.prm[9] * PI / 180.0;
        const Vec3 axis = p2 - p1;
        const double height = length(axis);
        if (height == 0.0 || a == 0.0) return false;
        const Vec3 w = axis * (1.0 / height);

        Vec3 r0 = p3 - p1;
        r0 = r0 - w * dot(r0, w);
        const double r0Len = length(r0);
        if (r0Len == 0.0) return false;
        r0 = r0 * (1.0 / r0Len);
        const Vec3 s = cross(w, r0);

        for (int k = 0; k < n; ++k) {
            const Vec3 d = pts[k] - p1;
            v[k] = dot(d, w) / height;
            u[k] = std::atan2(dot(d, s), dot(d, r0)) / a + 0.5;
        }
        fixSeam(u, n, a);
        break;
    }

    case TexMap::SPHERICAL: {
        // P1 centre, P2 where the texture centre touches, P3 forms with P1/P2 the
        // plane that bisects the texture horizontally. a, b = extents in degrees.
        const double a = t.prm[9] * PI / 180.0, b = t.prm[10] * PI / 180.0;
        if (a == 0.0 || b == 0.0) return false;

        Vec3 c = p2 - p1;
        const double cLen = length(c);
        if (cLen == 0.0) return false;
        c = c * (1.0 / cLen);

        Vec3 n1 = cross(c, p3 - p1);
        const double n1Len = length(n1);
        if (n1Len == 0.0) return false; // collinear
        n1 = n1 * (1.0 / n1Len);
        const Vec3 n2 = cross(n1, c);

        for (int k = 0; k < n; ++k) {
            const Vec3 d = pts[k] - p1;
            const double along = dot(d, c);
            u[k] = std::atan2(dot(d, n2), along) / a + 0.5;
            v[k] = std::atan2(dot(d, n1), along) / b + 0.5;
        }
        fixSeam(u, n, a);
        break;
    }
    }

    for (int k = 0; k < n; ++k) {
        uvOut[k][0] = static_cast<float>(u[k]);
        uvOut[k][1] = static_cast<float>(v[k]);
    }
    return true;
}

} // namespace ldc
