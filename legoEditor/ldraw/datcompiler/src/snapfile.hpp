#pragma once
// .snap file: the resolved snaps of one part, in LDU in the part's own space.
// Exactly the list parsePart(...)["connectors"] gives brickMaker.storeSnaps.
//
// Little-endian, sections 16-byte aligned, offsets absolute from the file start.
//
//   SnapHeader  (32 bytes)
//   snaps       SnapRecord[nSnaps]   (96 bytes each)
//   secs        SecRecord[nSecs]     (12 bytes each), referenced by secStart/secCount
//   strings     { uint16 len, utf8[len] } ...   SnapRecord string fields are byte
//               offsets from offStrings; offset 0 is always the empty string
//
// SnapRecord
//   float32 matrix[16]   4x4 row-major, rotation + translation (no scale); snap points along local -Y
//   float32 radius, length
//   uint32  secStart, secCount
//   uint32  group, ids, bounding       ids = sorted, comma-joined; bounding = space-joined tokens
//   uint8   kind                       0 CYL, 1 CLP, 2 FGR, 3 GEN, 4 SPH
//   uint8   gender                     'M' or 'F' (ASCII)
//   uint8   flags                      1 capA, 2 capB, 4 center, 8 slide
//   uint8   pad
//
// SecRecord
//   uint8   shape                      0 R, 1 A, 2 S, 3 _L, 4 L_, 5 M, 6 F (fingers)
//   uint8   pad[3]
//   float32 radius, length

#include "snap.hpp"

namespace ldc {

constexpr uint32_t SNAPFILE_VERSION = 1;

enum : uint8_t {
    SNAPREC_CAP_A  = 1u << 0,
    SNAPREC_CAP_B  = 1u << 1,
    SNAPREC_CENTER = 1u << 2,
    SNAPREC_SLIDE  = 1u << 3,
};

struct SnapHeader {
    char     magic[4];  // "SNAP"
    uint32_t version;
    uint32_t nSnaps, nSecs;
    uint32_t offSnaps, offSecs, offStrings, stringBytes;
};
static_assert(sizeof(SnapHeader) == 32, "SnapHeader must be 32 bytes");

struct SnapRecord {
    float    matrix[16];
    float    radius, length;
    uint32_t secStart, secCount;
    uint32_t group, ids, bounding;
    uint8_t  kind, gender, flags, pad;
};
static_assert(sizeof(SnapRecord) == 96, "SnapRecord must be 96 bytes");

struct SecRecord {
    uint8_t shape, pad[3];
    float   radius, length;
};
static_assert(sizeof(SecRecord) == 12, "SecRecord must be 12 bytes");

bool writeSnapFile(const fs::path& out, const std::vector<Snap>& snaps);

} // namespace ldc
