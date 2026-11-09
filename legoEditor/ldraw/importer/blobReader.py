"""
Reads .lego blobs written by ldcompile and builds Blender meshes from them.
Drop-in alongside brickMaker.py (legoEditor/ldraw/importer/blobReader.py):
the resulting mesh matches makeBrickMesh (native LDU, "col" and "tex" INT face
attributes, UVMap, shared or textured material), minus snaps for now.

Format: see datcompiler/src/blob.hpp
"""
import bpy

import numpy as np
from pathlib import Path

from . import materials, library

BLOB_HEADER = np.dtype([
    ("magic", "S4"), ("version", "<u4"), ("flags", "<u4"),
    ("nVerts", "<u4"), ("nLoops", "<u4"), ("nFaces", "<u4"), ("nTextures", "<u4"),
    ("offVerts", "<u4"), ("offLoops", "<u4"), ("offLoopStart", "<u4"),
    ("offFaceColour", "<u4"), ("offFaceFlags", "<u4"), ("offFaceTex", "<u4"), ("offUV", "<u4"),
    ("offTextures", "<u4"), ("texturesBytes", "<u4"),
    ("bboxMin", "<f4", 3), ("bboxMax", "<f4", 3),
    ("nSharpEdges", "<u4"), ("offSharpEdges", "<u4"),
])
assert BLOB_HEADER.itemsize == 96

BLOB_VERSION = 2
BLOB_HAS_TEXTURES = 1 << 0
BLOB_HAS_DIRECT_COLOUR = 1 << 1
BLOB_HAS_SHARP_EDGES = 1 << 2

FACE_DIRECT_COLOUR = 1 << 0  # faceColour is 0xRRGGBB, not an LDraw code
FACE_TEXTURED = 1 << 1

def readBlob(path):
    # Returns a dict of numpy arrays (views into the file bytes, no copies)
    buf = Path(path).read_bytes()
    h = np.frombuffer(buf, BLOB_HEADER, 1)[0]
    if h["magic"] != b"LEGO":
        raise ValueError(f"not a .lego blob: {path}")
    if h["version"] != BLOB_VERSION:
        raise ValueError(f"unsupported blob version {h['version']}: {path}")

    nV, nL, nF = int(h["nVerts"]), int(h["nLoops"]), int(h["nFaces"])

    def arr(off, dtype, count):
        if count == 0 or off == 0:
            return np.zeros(0, dtype)
        return np.frombuffer(buf, dtype, count, int(off))

    blob = {
        "flags": int(h["flags"]),
        "bboxMin": h["bboxMin"].copy(),
        "bboxMax": h["bboxMax"].copy(),
        "verts": arr(h["offVerts"], "<f4", nV * 3),
        "loops": arr(h["offLoops"], "<i4", nL),
        "loopStart": arr(h["offLoopStart"], "<i4", nF),
        "faceColour": arr(h["offFaceColour"], "<i4", nF),
        "faceFlags": arr(h["offFaceFlags"], "u1", nF),
        "faceTex": None,
        "uv": None,
        "textures": [],  # list of (texPath, glossPath or "")
        "sharpEdges": arr(h["offSharpEdges"], "<u4", int(h["nSharpEdges"]) * 2).reshape(-1, 2),
    }

    if blob["flags"] & BLOB_HAS_TEXTURES:
        blob["faceTex"] = arr(h["offFaceTex"], "<i4", nF)
        blob["uv"] = arr(h["offUV"], "<f4", nL * 2)
        pos = int(h["offTextures"])
        for _ in range(int(h["nTextures"])):
            pair = []
            for _ in range(2):
                n = int.from_bytes(buf[pos:pos + 2], "little")
                pair.append(buf[pos + 2:pos + 2 + n].decode("utf-8"))
                pos += 2 + n
            blob["textures"].append(tuple(pair))

    return blob

def sharpEdgeMask(blob, edgeVerts):
    # edgeVerts: (nEdges, 2) vertex indices from mesh.edges. Returns a bool mask of
    # the mesh edges listed in the blob's sharpEdges.
    nV = np.int64(len(blob["verts"]) // 3)
    sharp = blob["sharpEdges"]
    if len(sharp) == 0 or len(edgeVerts) == 0:
        return np.zeros(len(edgeVerts), dtype=bool)
    lo = np.minimum(edgeVerts[:, 0], edgeVerts[:, 1]).astype(np.int64)
    hi = np.maximum(edgeVerts[:, 0], edgeVerts[:, 1]).astype(np.int64)
    sharpKeys = sharp[:, 0].astype(np.int64) * nV + sharp[:, 1]  # already sorted
    keys = lo * nV + hi
    idx = np.clip(np.searchsorted(sharpKeys, keys), 0, len(sharpKeys) - 1)
    return sharpKeys[idx] == keys

def blobPath(brickId):
    # "3001", "3001.dat", "3001.DAT", "s\\3001s01.dat" -> <library>/blobs/3001.lego etc.
    # ldcompile writes lowercase names with forward slashes.
    root = library.getPartLibrary()
    if root is None:
        return None, None
    rel = brickId.strip().strip('"').replace("\\", "/").lower()
    if rel.endswith(".dat"):
        rel = rel[:-4]
    return Path(root), Path(root) / "blobs" / f"{rel}.lego"

def buildMesh(brickId):
    # Build a bpy mesh for a part id from its blob, or None if there is no blob.
    # The mesh is named like makeBrickMesh names it ("3001.dat"), so the
    # bpy.data.meshes.get(brickId) lookups in importer.py find it again.
    root, path = blobPath(brickId)
    if path is None or not path.exists():
        return None

    meshName = brickId if brickId.lower().endswith(".dat") else f"{brickId}.dat"

    blob = readBlob(path)
    mesh = bpy.data.meshes.new(meshName)
    nF = len(blob["loopStart"])
    if nF == 0:
        print(f"LDRAW BLOB: WARNING, '{meshName}' has no geometry")
        return mesh

    mesh.vertices.add(len(blob["verts"]) // 3)
    mesh.loops.add(len(blob["loops"]))
    mesh.polygons.add(nF)
    mesh.vertices.foreach_set("co", blob["verts"])
    mesh.loops.foreach_set("vertex_index", blob["loops"])
    mesh.polygons.foreach_set("loop_start", blob["loopStart"])

    mesh.attributes.new("col", 'INT', 'FACE').data.foreach_set("value", blob["faceColour"])
    mesh.attributes.new("colFlags", 'INT', 'FACE').data.foreach_set("value", blob["faceFlags"].astype(np.int32))

    if blob["faceTex"] is not None:
        mesh.attributes.new("tex", 'INT', 'FACE').data.foreach_set("value", blob["faceTex"])
        mesh.uv_layers.new(name="UVMap").data.foreach_set("uv", blob["uv"])
        texPaths = []
        for tex, _gloss in blob["textures"]:
            p = Path(tex)
            texPaths.append(str(p if p.is_absolute() else root / p))
        mesh.materials.append(materials.makeTexturedMaterial(f"{meshName}_tex", texPaths))
    else:
        mesh.materials.append(materials.getSharedMaterial())

    mesh.update(calc_edges=True)

    # Smooth shading everywhere; LDraw edge lines decide the hard edges
    nE = len(mesh.edges)
    edgeVerts = np.empty(nE * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edgeVerts)
    sharp = sharpEdgeMask(blob, edgeVerts.reshape(-1, 2))
    attr = mesh.attributes.get("sharp_edge") or mesh.attributes.new("sharp_edge", 'BOOLEAN', 'EDGE')
    attr.data.foreach_set("value", sharp)

    return mesh
