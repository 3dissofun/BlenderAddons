"""
Provides tools to map and decode pieceIndex.bin, and to load bricks from the
chunked library files.

pieceIndex.bin layout: fixed 64-byte little-endian records, record N has id N
    uint32 id (== record number, so id -> name is a direct seek)
    uint32 nameLen
    char[56] name (UTF-8, zero padded)

Library chunk files: LIBRARY_DIR/ldrawLibrary_<chunk>.blend, chunk = id // CHUNK.
Meshes and objects in them are named f"{id:0{ID_WIDTH}d}_{name}", e.g. "04315_3001".
Objects carry (see prepareChunks.py):
    lego_piece     INT, piece id
    lego_ref       STR, part name ("3001")
    lego_embedded  BOOL, always False for library parts
    is_lego        False (a prototype, not a brick in the scene)
    location       (id, 0, 0), which the point cloud node group reads the id from
"""
from functools import lru_cache
from pathlib import Path
import struct
import os

import bpy

EXTERNAL = Path(__file__).parent.parent / "external"

LIBRARY_DIR = EXTERNAL / "ldrawLibrary"
CHUNK = 1000 # Pieces per blend file

INDEX_PATH = EXTERNAL / "ldrawIndex.bin"
ID_WIDTH = 5 # Size of the index in digits
RECORD = 64 # Size in bytes of the record


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load():
    # Read the whole index once into memory. Returns (names, ids)
    data = INDEX_PATH.read_bytes()
    names = []
    for off in range(0, len(data), RECORD):
        _, n = struct.unpack_from("<II", data, off)
        names.append(data[off + 8: off + 8 + n].decode())
    ids = {name.lower(): i for i, name in enumerate(names)}
    return names, ids

def idFor(brickName):
    key = brickName.lower().removesuffix(".dat")
    return _load()[1].get(key)

def nameFor(pieceId):
    names = _load()[0]
    return names[pieceId] if 0 <= pieceId < len(names) else None

def dataNameFor(pieceId):
    # 4315 -> '04315_3001' (the mesh/object name in the library)
    return f"{pieceId:0{ID_WIDTH}d}_{nameFor(pieceId)}"

def objectNameFor(brickName):
    # '3001' -> '04315_3001'
    i = idFor(brickName)
    return None if i is None else dataNameFor(i)


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------

def _chunkPath(chunk):
    return LIBRARY_DIR / f"ldrawLibrary_{chunk:03d}.blend"

def _norm(p):
    return os.path.normcase(os.path.abspath(bpy.path.abspath(str(p))))

def _linked(collection):
    # {normalised library path: {name: datablock}} for everything linked so far
    libPaths = {lib: _norm(lib.filepath) for lib in bpy.data.libraries}
    out = {}
    for d in collection:
        if d.library is not None:
            out.setdefault(libPaths[d.library], {})[d.name] = d
    return out

def _appendData(kind, brickNames, link):
    # Shared by appendMeshes / appendObjects. kind is "meshes" or "objects".
    # One libraries.load per chunk file touched; returns {brickName: datablock}.

    dataNames = {}                                # brickName -> library name
    byChunk = {}                                  # chunk -> {library names}
    for b in brickNames:
        i = idFor(b)
        if i is None:
            continue
        n = dataNameFor(i)
        dataNames[b] = n
        byChunk.setdefault(i // CHUNK, set()).add(n)

    linked = _linked(getattr(bpy.data, kind)) if link else {}
    loaded = {}
    for chunk, names in byChunk.items():
        path = _chunkPath(chunk)
        if not path.is_file():
            continue
        if link:
            have = linked.get(_norm(path), {})
            loaded.update((n, have[n]) for n in names if n in have)
            names = names - have.keys()
            if not names:
                continue

        with bpy.data.libraries.load(str(path), link=link) as (dataFrom, dataTo):
            available = set(getattr(dataFrom, kind))
            found = [n for n in names if n in available]
            setattr(dataTo, kind, list(found))    # copy: Blender fills this list in place
        if names and not found:
            print(f"LDRAW LIBRARY: WARNING, none of {len(names)} {kind} found in {path.name}")
        loaded.update((n, d) for n, d in zip(found, getattr(dataTo, kind)) if d is not None)

    return {b: loaded[n] for b, n in dataNames.items() if n in loaded}

def appendMeshes(brickNames, link=True):
    # Meshes for several bricks, one pass per chunk file touched.
    # Returns {brickName: mesh} for the bricks the library has.
    return _appendData("meshes", brickNames, link)

def appendMesh(brickName, link=True):
    # The mesh for one brick, or None if the library doesn't have it
    return appendMeshes([brickName], link).get(brickName)

def appendObjects(brickNames, link=True):
    # Ready-made prototype objects for several bricks (their meshes come with
    # them), one pass per chunk file touched. Returns {brickName: object}.
    # Linked objects are read-only: their properties come from prepareChunks.py.
    return _appendData("objects", brickNames, link)

def appendObject(brickName, link=True):
    return appendObjects([brickName], link).get(brickName)
