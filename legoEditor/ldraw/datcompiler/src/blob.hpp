#pragma once
// .lego blob: one flattened part, laid out to go straight into Blender with
// Mesh.vertices/loops/polygons.foreach_set and attribute foreach_set calls.
//
// Little-endian. All offsets are absolute byte offsets from the start of the file,
// 16-byte aligned. An absent section has offset 0. Coordinates are native LDU
// (LDraw axes), matching brickMaker.py.
//
//   BlobHeader (96 bytes)
//   verts        float32[nVerts * 3]   welded positions          -> vertices "co"
//   loops        int32  [nLoops]       vertex index per corner   -> loops "vertex_index"
//   loopStart    int32  [nFaces]       first loop of each face   -> polygons "loop_start"
//                                      (face size = next loopStart - this one, last ends at nLoops)
//   faceColour   int32  [nFaces]       LDraw code, or 0xRRGGBB    -> "col" INT FACE attribute
//   faceFlags    uint8  [nFaces]       FACE_DIRECT_COLOUR | FACE_TEXTURED
//   faceTex      int32  [nFaces]       index into the texture table, -1 = none   (BLOB_HAS_TEXTURES)
//   uv           float32[nLoops * 2]   per loop, already in Blender convention    (BLOB_HAS_TEXTURES)
//                                      (v flipped); 0,0 on untextured faces
//   textures     nTextures x { uint16 len, utf8[len] texPath, uint16 len, utf8[len] glossPath }
//                                      paths relative to the LDraw root when inside it;
//                                      glossPath len 0 = no glossmap
//   sharpEdges   uint32[nSharpEdges * 2]  welded vertex pairs (lo, hi), sorted, unique  (v2+)
//                                      mesh edges covered by LDraw type 2 lines -> "sharp_edge"

#include "parser.hpp"

#include <cstdint>

namespace ldc {

constexpr uint32_t BLOB_VERSION = 2; // v2: sharpEdges (v1 files have zeros in those fields)

enum : uint32_t {
    BLOB_HAS_TEXTURES      = 1u << 0, // faceTex, uv and textures sections are present
    BLOB_HAS_DIRECT_COLOUR = 1u << 1, // at least one face uses FACE_DIRECT_COLOUR
    BLOB_HAS_SHARP_EDGES   = 1u << 2, // sharpEdges section is present
};

struct BlobHeader {
    char     magic[4];      // "LEGO"
    uint32_t version;
    uint32_t flags;
    uint32_t nVerts, nLoops, nFaces, nTextures;
    uint32_t offVerts, offLoops, offLoopStart, offFaceColour, offFaceFlags, offFaceTex, offUV;
    uint32_t offTextures, texturesBytes;
    float    bboxMin[3], bboxMax[3];
    uint32_t nSharpEdges, offSharpEdges; // were reserved[2] in v1
};
static_assert(sizeof(BlobHeader) == 96, "BlobHeader must be 96 bytes");

struct BlobStats {
    size_t verts = 0, loops = 0, faces = 0, droppedFaces = 0;
    size_t edgeLines = 0;        // type 2 lines in the part
    size_t edgesDirect = 0;      // matched one mesh edge exactly
    size_t edgesWalked = 0;      // matched a chain of collinear mesh edges (T-junctions)
    size_t edgesUnmatched = 0;   // no mesh edge path found (floating lines, gaps)
    size_t sharpEdges = 0;       // unique mesh edges marked sharp
    size_t quadsSplit = 0;       // non-planar quads split along an edge-line diagonal
};

// Welds vertices within weldDist (like bmesh remove_doubles), drops faces that
// collapse, matches edge lines to mesh edges, and writes the blob. Texture
// indices are remapped to a per-blob table.
bool writeBlob(const fs::path& out, const Geometry& geom, const std::vector<TextureEntry>& textures,
               double weldDist, BlobStats* stats = nullptr);

} // namespace ldc
