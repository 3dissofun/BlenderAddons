# ldcompile

Flattens LDraw `.dat` parts into `.lego` blobs that build a Blender mesh with a
handful of `foreach_set` calls.

## Build

```
cmake -S . -B build
cmake --build build --config Release
```

Needs C++17 with floating-point `std::from_chars` (MSVC 2019+, GCC 11+, recent Clang).

## Run

```
ldcompile <ldraw>/parts
```

writes `<ldraw>/blobs/<part>.lego` for every `.dat` in `parts/`. The LDraw root is
found by searching upward for a folder containing `p/` and `parts/`.

| option | |
|---|---|
| `--lib DIR` | LDraw root, if it can't be found automatically |
| `--out DIR` | output folder (default `<input>/../blobs`) |
| `-r` | include sub-folders (`parts/s` -> `blobs/s`) |
| `--weld D` | weld distance in LDU, default 0.05 (same as brickMaker), 0 = off |
| `--cache-mb N` | memory for cached sub-files, default 1024 |
| `--ldr` | also write a flattened `.ldr` beside each blob, to inspect in LDView |
| `-q` | no progress line |

A single `.dat` file also works as the input.

## What gets baked

- Every type 1 reference flattened into triangles and quads, native LDU coordinates
- Winding: all faces CCW (BFC CW, INVERTNEXT and mirrored matrices resolved per level)
- Vertices welded; faces that collapse are dropped
- Per-face colour: LDraw code (16 kept for "inherit") or `0xRRGGBB` with `FACE_DIRECT_COLOUR`
- Sharp edges from LDraw type 2 lines: each line is matched to the welded mesh edge it
  lies on, or to a chain of collinear edges across T-junctions, and stored as vertex
  pairs. Non-planar quads with an edge line across a diagonal are split along it.
  The reader shades everything smooth and sets the `sharp_edge` attribute.
- `!TEXMAP` PLANAR / CYLINDRICAL / SPHERICAL, including NEXT, FALLBACK, `0 !:` and GLOSSMAP:
  per-face texture index and per-loop UVs (v already flipped for Blender)

Not yet: snaps (shadow library).

## Format

See `src/blob.hpp` for the full layout. `blender/blobReader.py` reads it
(`readBlob` is numpy only; `buildMesh` makes the bpy mesh the same way `makeBrickMesh` does).
