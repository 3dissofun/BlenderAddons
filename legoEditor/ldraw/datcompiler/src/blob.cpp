#include "blob.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <limits>
#include <unordered_map>

namespace ldc {

namespace {

// Spatial hash for welding. Cells are 2 * dist wide, so any point within dist of
// p lies in one of at most 2 cells per axis: 8 lookups per vertex.
class Welder {
public:
    explicit Welder(double dist, size_t expected = 0)
        : dist2_(dist * dist), radius_(dist), cell_(dist > 0.0 ? dist * 2.0 : 1.0) {
        exact_.reserve(expected);
        cells_.reserve(expected);
        next_.reserve(expected);
        verts.reserve(expected * 3);
    }

    // Existing vertex within dist of p, or -1
    int64_t find(const float* p) const {
        // Fast path: most LDraw vertices are bit-identical copies of an earlier one
        if (auto it = exact_.find(exactKey(p)); it != exact_.end()) return it->second;
        if (dist2_ == 0.0) return -1;

        int64_t lo[3], hi[3];
        for (int a = 0; a < 3; ++a) {
            lo[a] = cellOf(p[a] - radius_);
            hi[a] = cellOf(p[a] + radius_);
        }
        for (int64_t x = lo[0]; x <= hi[0]; ++x)
            for (int64_t y = lo[1]; y <= hi[1]; ++y)
                for (int64_t z = lo[2]; z <= hi[2]; ++z) {
                    auto it = cells_.find(cellKey(x, y, z));
                    if (it == cells_.end()) continue;
                    for (uint32_t idx = it->second; idx != NONE; idx = next_[idx]) {
                        const float* q = &verts[3 * idx];
                        const double dx = p[0] - q[0], dy = p[1] - q[1], dz = p[2] - q[2];
                        if (dx * dx + dy * dy + dz * dz <= dist2_) return idx;
                    }
                }
        return -1;
    }

    uint32_t add(const float* p) {
        if (const int64_t found = find(p); found >= 0) return static_cast<uint32_t>(found);
        const auto idx = static_cast<uint32_t>(verts.size() / 3);
        verts.insert(verts.end(), p, p + 3);
        exact_.emplace(exactKey(p), idx);
        // Prepend to the cell's linked list: no per-cell allocations
        auto [it, inserted] = cells_.emplace(cellKey(cellOf(p[0]), cellOf(p[1]), cellOf(p[2])), idx);
        next_.push_back(inserted ? NONE : it->second);
        if (!inserted) it->second = idx;
        return idx;
    }

    Vec3 pos(uint32_t i) const { return {verts[3 * i], verts[3 * i + 1], verts[3 * i + 2]}; }

    std::vector<float> verts;

private:
    static constexpr uint32_t NONE = 0xFFFFFFFFu;

    struct ExactKey {
        uint32_t x, y, z;
        bool operator==(const ExactKey& o) const { return x == o.x && y == o.y && z == o.z; }
    };
    struct ExactHash {
        size_t operator()(const ExactKey& k) const {
            uint64_t h = (static_cast<uint64_t>(k.x) * 0x9E3779B97F4A7C15ull) ^ (static_cast<uint64_t>(k.y) * 0xC2B2AE3D27D4EB4Full) ^
                         (static_cast<uint64_t>(k.z) * 0x165667B19E3779F9ull);
            return static_cast<size_t>(h ^ (h >> 29));
        }
    };
    static ExactKey exactKey(const float* p) {
        // +0.0f normalises -0 so it matches 0
        ExactKey k;
        const float v[3] = {p[0] + 0.0f, p[1] + 0.0f, p[2] + 0.0f};
        std::memcpy(&k, v, sizeof k);
        return k;
    }

    int64_t cellOf(double v) const { return static_cast<int64_t>(std::floor(v / cell_)); }
    static uint64_t cellKey(int64_t x, int64_t y, int64_t z) {
        // 21 bits per axis; wrap-around collisions only cost extra distance checks
        const uint64_t m = (1ull << 21) - 1;
        return ((static_cast<uint64_t>(x) & m) << 42) | ((static_cast<uint64_t>(y) & m) << 21) |
               (static_cast<uint64_t>(z) & m);
    }

    double dist2_;
    double radius_;
    double cell_;
    std::unordered_map<ExactKey, uint32_t, ExactHash> exact_;
    std::unordered_map<uint64_t, uint32_t> cells_;   // cell -> most recent vertex in it
    std::vector<uint32_t> next_;                      // per vertex: next vertex in the same cell
};

uint64_t edgeKey(uint32_t a, uint32_t b) {
    if (a > b) std::swap(a, b);
    return (static_cast<uint64_t>(a) << 32) | b;
}

// Marks the mesh edges covered by LDraw edge lines. An edge line usually matches one
// mesh edge, but where faces have T-junctions it spans several collinear ones, so we
// walk from A towards B along mesh edges that stay on the segment.
void matchEdges(const std::vector<std::pair<int64_t, int64_t>>& edges, const Welder& welder, const std::vector<int32_t>& loops,
                const std::vector<int32_t>& loopStart, double tol, std::vector<uint64_t>& sharp,
                BlobStats& st) {
    const size_t nV = welder.verts.size() / 3;
    if (edges.empty() || nV == 0) return;

    // Vertex adjacency from face boundaries, as CSR (offsets + flat neighbour list)
    std::vector<uint32_t> adjOff(nV + 1, 0), adjList;
    {
        std::vector<uint64_t> pairs; // both directions, (from << 32) | to
        pairs.reserve(loops.size() * 2);
        for (size_t f = 0; f < loopStart.size(); ++f) {
            const size_t s = static_cast<size_t>(loopStart[f]);
            const size_t e = (f + 1 < loopStart.size()) ? static_cast<size_t>(loopStart[f + 1]) : loops.size();
            for (size_t i = s; i < e; ++i) {
                const auto a = static_cast<uint64_t>(loops[i]);
                const auto b = static_cast<uint64_t>(loops[(i + 1 < e) ? i + 1 : s]);
                pairs.push_back((a << 32) | b);
                pairs.push_back((b << 32) | a);
            }
        }
        std::sort(pairs.begin(), pairs.end());
        pairs.erase(std::unique(pairs.begin(), pairs.end()), pairs.end());
        adjList.resize(pairs.size());
        for (size_t i = 0; i < pairs.size(); ++i) {
            ++adjOff[(pairs[i] >> 32) + 1];
            adjList[i] = static_cast<uint32_t>(pairs[i] & 0xFFFFFFFFu);
        }
        for (size_t v = 0; v < nV; ++v) adjOff[v + 1] += adjOff[v];
    }
    auto neighbours = [&](uint32_t v) {
        return std::pair<const uint32_t*, const uint32_t*>(adjList.data() + adjOff[v], adjList.data() + adjOff[v + 1]);
    };

    const double tol2 = tol * tol;
    std::vector<uint64_t> path;

    for (const auto& [ia, ib] : edges) {
        ++st.edgeLines;
        if (ia < 0 || ib < 0) { ++st.edgesUnmatched; continue; }
        if (ia == ib) continue; // zero-length after welding

        const auto a = static_cast<uint32_t>(ia), b = static_cast<uint32_t>(ib);
        const Vec3 A = welder.pos(a);
        const Vec3 AB = welder.pos(b) - A;
        const double len = length(AB);
        const Vec3 dir = AB * (1.0 / len);

        path.clear();
        uint32_t cur = a;
        double tCur = 0.0;
        bool reached = false;
        for (int step = 0; step < 512; ++step) {
            // Next neighbour along the segment: smallest forward progress, on the line
            int64_t best = -1;
            double bestT = 0.0;
            const auto [nbBegin, nbEnd] = neighbours(cur);
            for (const uint32_t* np = nbBegin; np != nbEnd; ++np) {
                const uint32_t nb = *np;
                if (nb == b) { best = nb; bestT = len; break; }
                const Vec3 v = welder.pos(nb) - A;
                const double t = dot(v, dir);
                if (t <= tCur + 1e-6 || t > len + tol) continue;
                if (dot(v, v) - t * t > tol2) continue;
                if (best < 0 || t < bestT) { best = nb; bestT = t; }
            }
            // Prefer stepping straight to b only if nothing lies between
            if (best >= 0 && static_cast<uint32_t>(best) == b) {
                for (const uint32_t* np = nbBegin; np != nbEnd; ++np) {
                    const uint32_t nb = *np;
                    if (nb == b) continue;
                    const Vec3 v = welder.pos(nb) - A;
                    const double t = dot(v, dir);
                    if (t > tCur + 1e-6 && t < len - 1e-6 && dot(v, v) - t * t <= tol2 && t < bestT) {
                        best = nb;
                        bestT = t;
                    }
                }
            }
            if (best < 0) break;
            path.push_back(edgeKey(cur, static_cast<uint32_t>(best)));
            cur = static_cast<uint32_t>(best);
            tCur = bestT;
            if (cur == b) { reached = true; break; }
        }

        if (!reached) { ++st.edgesUnmatched; continue; }
        (path.size() == 1 ? st.edgesDirect : st.edgesWalked)++;
        sharp.insert(sharp.end(), path.begin(), path.end());
    }

    std::sort(sharp.begin(), sharp.end());
    sharp.erase(std::unique(sharp.begin(), sharp.end()), sharp.end());
}

void align16(std::vector<char>& buf) {
    buf.resize((buf.size() + 15) & ~static_cast<size_t>(15), 0);
}

template <typename T>
uint32_t appendSection(std::vector<char>& buf, const std::vector<T>& data) {
    if (data.empty()) return 0;
    align16(buf);
    const auto off = static_cast<uint32_t>(buf.size());
    const size_t bytes = data.size() * sizeof(T);
    buf.resize(buf.size() + bytes);
    std::memcpy(buf.data() + off, data.data(), bytes);
    return off;
}

void appendString(std::vector<char>& buf, const std::string& s) {
    const auto len = static_cast<uint16_t>(std::min<size_t>(s.size(), 0xFFFF));
    char lenBytes[2] = {static_cast<char>(len & 0xFF), static_cast<char>(len >> 8)};
    buf.insert(buf.end(), lenBytes, lenBytes + 2);
    buf.insert(buf.end(), s.data(), s.data() + len);
}

} // namespace

bool writeBlob(const fs::path& out, const Geometry& geom, const std::vector<TextureEntry>& textures,
               double weldDist, BlobStats* stats) {
    const FaceList& faces = geom.faces;
    Welder welder(weldDist, faces.size() * 2);
    BlobStats st;

    std::vector<int32_t> loops, loopStart, faceColour, faceTex;
    std::vector<uint8_t> faceFlags;
    std::vector<float> uv;
    loops.reserve(faces.size() * 4);
    loopStart.reserve(faces.size());
    faceColour.reserve(faces.size());
    faceFlags.reserve(faces.size());
    faceTex.reserve(faces.size());
    uv.reserve(faces.size() * 8);

    std::unordered_map<int32_t, int32_t> localTex; // global texture index -> blob index
    std::vector<int32_t> localTexOrder;
    bool anyTextured = false, anyDirect = false;
    size_t dropped = 0;

    // Pass 1: weld every face, dropping corners that collapsed onto a neighbour
    struct WFace {
        uint32_t idx[4];
        uint8_t corner[4]; // which corner of the source face each welded corner came from
        uint8_t m;
        uint32_t src;
    };
    std::vector<WFace> wfaces;
    wfaces.reserve(faces.size() + faces.size() / 8);
    for (size_t fi = 0; fi < faces.size(); ++fi) {
        const Face& f = faces[fi];
        WFace w{};
        w.src = static_cast<uint32_t>(fi);
        int m = 0;
        for (int k = 0; k < f.n; ++k) {
            const uint32_t vi = welder.add(f.p[k]);
            if (m > 0 && w.idx[m - 1] == vi) continue;
            w.idx[m] = vi;
            w.corner[m] = static_cast<uint8_t>(k);
            ++m;
        }
        if (m > 1 && w.idx[m - 1] == w.idx[0]) --m;
        bool degenerate = m < 3;
        for (int i = 0; !degenerate && i < m; ++i)
            for (int j = i + 1; j < m; ++j)
                if (w.idx[i] == w.idx[j]) { degenerate = true; break; }
        if (degenerate) { ++dropped; continue; }
        w.m = static_cast<uint8_t>(m);
        wfaces.push_back(w);
    }

    // Edge line endpoints -> welded vertex (or -1), looked up once
    std::vector<std::pair<int64_t, int64_t>> edgeEnds;
    edgeEnds.reserve(geom.edges.size());
    for (const Edge& el : geom.edges) edgeEnds.emplace_back(welder.find(el.p[0]), welder.find(el.p[1]));

    // Pass 2: an edge line across a quad's diagonal marks the fold of a non-planar
    // quad. Split the quad along it, so the fold exists as a real edge (and folds the
    // way the author drew it, not whichever way Blender would triangulate).
    if (!geom.edges.empty()) {
        std::unordered_map<uint64_t, uint32_t> diagToQuad; // diagonal -> wface index
        for (size_t i = 0; i < wfaces.size(); ++i) {
            const WFace& w = wfaces[i];
            if (w.m != 4) continue;
            diagToQuad.emplace(edgeKey(w.idx[0], w.idx[2]), static_cast<uint32_t>(i));
            diagToQuad.emplace(edgeKey(w.idx[1], w.idx[3]), static_cast<uint32_t>(i));
        }
        if (!diagToQuad.empty()) {
            std::vector<uint8_t> split(wfaces.size(), 0); // 0 none, 1 split 0-2, 2 split 1-3
            for (const auto& [ia, ib] : edgeEnds) {
                if (ia < 0 || ib < 0 || ia == ib) continue;
                auto it = diagToQuad.find(edgeKey(static_cast<uint32_t>(ia), static_cast<uint32_t>(ib)));
                if (it == diagToQuad.end() || split[it->second]) continue;
                const WFace& w = wfaces[it->second];
                const bool d02 = edgeKey(w.idx[0], w.idx[2]) == it->first;
                split[it->second] = d02 ? 1 : 2;
            }
            const size_t n = wfaces.size();
            for (size_t i = 0; i < n; ++i) {
                if (!split[i]) continue;
                const WFace q = wfaces[i];
                const int s0 = (split[i] == 1) ? 0 : 1; // split from corner s0 to s0 + 2
                auto tri = [&](int c0, int c1, int c2) {
                    WFace t{};
                    t.m = 3;
                    t.src = q.src;
                    const int cs[3] = {c0, c1, c2};
                    for (int k = 0; k < 3; ++k) {
                        t.idx[k] = q.idx[cs[k] % 4];
                        t.corner[k] = q.corner[cs[k] % 4];
                    }
                    return t;
                };
                wfaces[i] = tri(s0, s0 + 1, s0 + 2);           // same winding as the quad
                wfaces.push_back(tri(s0, s0 + 2, s0 + 3));
                ++st.quadsSplit;
            }
        }
    }

    // Pass 3: emit loops and per-face attributes
    for (const WFace& w : wfaces) {
        const Face& f = faces[w.src];
        const int m = w.m;

        loopStart.push_back(static_cast<int32_t>(loops.size()));
        const bool textured = (f.flags & FACE_TEXTURED) && f.tex >= 0;
        for (int k = 0; k < m; ++k) {
            loops.push_back(static_cast<int32_t>(w.idx[k]));
            if (textured) {
                uv.push_back(f.uv[w.corner[k]][0]);
                uv.push_back(1.0f - f.uv[w.corner[k]][1]); // LDraw image origin is top-left
            } else {
                uv.push_back(0.0f);
                uv.push_back(0.0f);
            }
        }

        faceColour.push_back(f.colour);
        faceFlags.push_back(static_cast<uint8_t>(f.flags & FACE_FLAGS_WRITTEN & (textured ? 0xFF : ~FACE_TEXTURED)));
        anyDirect |= (f.flags & FACE_DIRECT_COLOUR) != 0;

        if (textured) {
            anyTextured = true;
            auto [it, inserted] = localTex.emplace(f.tex, static_cast<int32_t>(localTexOrder.size()));
            if (inserted) localTexOrder.push_back(f.tex);
            faceTex.push_back(it->second);
        } else {
            faceTex.push_back(-1);
        }
    }

    if (!anyTextured) {
        faceTex.clear();
        uv.clear();
    }

    // Edge lines -> sharp mesh edges. Off-line tolerance follows the weld distance.
    std::vector<uint64_t> sharpKeys;
    matchEdges(edgeEnds, welder, loops, loopStart, std::max(weldDist, 1e-3), sharpKeys, st);
    std::vector<uint32_t> sharpPairs;
    sharpPairs.reserve(sharpKeys.size() * 2);
    for (uint64_t k : sharpKeys) {
        sharpPairs.push_back(static_cast<uint32_t>(k >> 32));
        sharpPairs.push_back(static_cast<uint32_t>(k & 0xFFFFFFFFu));
    }

    BlobHeader h{};
    std::memcpy(h.magic, "LEGO", 4);
    h.version = BLOB_VERSION;
    h.flags = (anyTextured ? BLOB_HAS_TEXTURES : 0u) | (anyDirect ? BLOB_HAS_DIRECT_COLOUR : 0u) |
              (sharpKeys.empty() ? 0u : BLOB_HAS_SHARP_EDGES);
    h.nSharpEdges = static_cast<uint32_t>(sharpKeys.size());
    h.nVerts = static_cast<uint32_t>(welder.verts.size() / 3);
    h.nLoops = static_cast<uint32_t>(loops.size());
    h.nFaces = static_cast<uint32_t>(loopStart.size());
    h.nTextures = static_cast<uint32_t>(localTexOrder.size());

    if (h.nVerts > 0) {
        for (int a = 0; a < 3; ++a) {
            h.bboxMin[a] = std::numeric_limits<float>::max();
            h.bboxMax[a] = std::numeric_limits<float>::lowest();
        }
        for (size_t i = 0; i < welder.verts.size(); i += 3)
            for (int a = 0; a < 3; ++a) {
                h.bboxMin[a] = std::min(h.bboxMin[a], welder.verts[i + a]);
                h.bboxMax[a] = std::max(h.bboxMax[a], welder.verts[i + a]);
            }
    }

    std::vector<char> buf(sizeof(BlobHeader), 0);
    h.offVerts = appendSection(buf, welder.verts);
    h.offLoops = appendSection(buf, loops);
    h.offLoopStart = appendSection(buf, loopStart);
    h.offFaceColour = appendSection(buf, faceColour);
    h.offFaceFlags = appendSection(buf, faceFlags);
    h.offFaceTex = appendSection(buf, faceTex);
    h.offUV = appendSection(buf, uv);

    if (!localTexOrder.empty()) {
        align16(buf);
        h.offTextures = static_cast<uint32_t>(buf.size());
        for (int32_t g : localTexOrder) {
            appendString(buf, textures[static_cast<size_t>(g)].tex);
            appendString(buf, textures[static_cast<size_t>(g)].gloss);
        }
        h.texturesBytes = static_cast<uint32_t>(buf.size() - h.offTextures);
    }
    h.offSharpEdges = appendSection(buf, sharpPairs);

    std::memcpy(buf.data(), &h, sizeof h);

    std::error_code ec;
    fs::create_directories(out.parent_path(), ec);
    std::ofstream o(out, std::ios::binary);
    if (!o) return false;
    o.write(buf.data(), static_cast<std::streamsize>(buf.size()));
    if (!o) return false;

    st.verts = h.nVerts;
    st.loops = h.nLoops;
    st.faces = h.nFaces;
    st.droppedFaces = dropped;
    st.sharpEdges = sharpKeys.size();
    if (stats) *stats = st;
    return true;
}

} // namespace ldc
