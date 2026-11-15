"""
Reads .snap files written by ldcompile (--shadow) and stores them on brick meshes,
giving the same mesh.lego_snaps as brickMaker.storeSnaps.
Lives next to blobReader.py (legoEditor/ldraw/importer/snapReader.py).

Format: see datcompiler/src/snapfile.hpp
"""
from ... import utils
from . import blobReader, brickMaker, library, parser

from pathlib import Path
import numpy as np

SNAP_HEADER = np.dtype([
    ("magic", "S4"), ("version", "<u4"), ("nSnaps", "<u4"), ("nSecs", "<u4"),
    ("offSnaps", "<u4"), ("offSecs", "<u4"), ("offStrings", "<u4"), ("stringBytes", "<u4"),
])
SNAP_RECORD = np.dtype([
    ("matrix", "<f4", 16), ("radius", "<f4"), ("length", "<f4"),
    ("secStart", "<u4"), ("secCount", "<u4"),
    ("group", "<u4"), ("ids", "<u4"), ("bounding", "<u4"),
    ("kind", "u1"), ("gender", "u1"), ("flags", "u1"), ("pad", "u1"),
])
SEC_RECORD = np.dtype([("shape", "u1"), ("pad", "u1", 3), ("radius", "<f4"), ("length", "<f4")])
assert SNAP_HEADER.itemsize == 32 and SNAP_RECORD.itemsize == 96 and SEC_RECORD.itemsize == 12

SNAPFILE_VERSION = 1
KINDS = ("CYL", "CLP", "FGR", "GEN", "SPH")      # storeSnaps uses kind[5:] of "SNAP_CYL"
SHAPES = ("R", "A", "S", "_L", "L_", "M", "F")
CAP_A, CAP_B, CENTER, SLIDE = 1, 2, 4, 8

def readSnaps(path):
    # Returns (records, secs, string(offset) -> str). records/secs are numpy views.
    buf = Path(path).read_bytes()
    h = np.frombuffer(buf, SNAP_HEADER, 1)[0]
    if h["magic"] != b"SNAP":
        raise ValueError(f"not a .snap file: {path}")
    if h["version"] != SNAPFILE_VERSION:
        raise ValueError(f"unsupported .snap version {h['version']}: {path}")

    nS, nC = int(h["nSnaps"]), int(h["nSecs"])
    recs = np.frombuffer(buf, SNAP_RECORD, nS, int(h["offSnaps"])) if nS else np.zeros(0, SNAP_RECORD)
    secs = np.frombuffer(buf, SEC_RECORD, nC, int(h["offSecs"])) if nC else np.zeros(0, SEC_RECORD)
    base = int(h["offStrings"])

    cache = {}
    def string(off):
        off = int(off)
        s = cache.get(off)
        if s is None:
            p = base + off
            n = int.from_bytes(buf[p:p + 2], "little")
            s = cache[off] = buf[p + 2:p + 2 + n].decode("utf-8")
        return s
    return recs, secs, string

def snapPath(brickId):
    # <library>/blobs/<id>.snap, next to the .lego blob
    root, blob = blobReader.blobPath(brickId)
    return None if blob is None else blob.with_suffix(".snap")

def storeSnapsFromFile(mesh, path):
    # Same fields as brickMaker.storeSnaps. Returns the number of snaps stored.
    recs, secs, string = readSnaps(path)
    coll = mesh.lego_snaps
    coll.clear()
    for r in recs:
        item = coll.add()
        item.kind = KINDS[r["kind"]]
        item.gender = chr(r["gender"])
        item.group = string(r["group"])
        item.ids = string(r["ids"])
        item.matrix = r["matrix"]
        flags = int(r["flags"])
        item.capA = bool(flags & CAP_A)
        item.capB = bool(flags & CAP_B)
        item.center = bool(flags & CENTER)
        item.slide = bool(flags & SLIDE)
        item.radius = float(r["radius"])
        item.length = float(r["length"])
        item.bounding = string(r["bounding"])
        item.extra = ""
        s0 = int(r["secStart"])
        for sec in secs[s0:s0 + int(r["secCount"])]:
            si = item.secs.add()
            si.shape = SHAPES[sec["shape"]]
            si.radius = float(sec["radius"])
            si.length = float(sec["length"])
    return len(recs)

def snapsCompiled():
    # True if ldcompile ran with --shadow for this library (it writes blobs/snaps.info)
    root, _ = blobReader.blobPath("x")
    return root is not None and (root / "blobs" / "snaps.info").exists()

def buildSnaps(meshes, rebuild=False):
    # Fill lego_snaps on each brick mesh from its .snap file.
    # If snaps were compiled, a part with a blob but no .snap has no snaps.
    # Anything else (custom or MPD-embedded parts, or snaps never compiled) falls
    # back to the Python parser + shadow library.
    compiled = snapsCompiled()
    for mesh in meshes:
        if len(mesh.lego_snaps) and not rebuild:
            continue  # already built
        partId = utils.stripBlendSuffix(mesh.name)

        sp = snapPath(partId)
        if sp is not None and sp.exists():
            storeSnapsFromFile(mesh, sp)
            continue

        _root, blob = blobReader.blobPath(partId)
        if compiled and blob is not None and blob.exists():
            mesh.lego_snaps.clear()  # compiled part with no snaps
            continue

        partPath = library.resolvePart(partId)
        if partPath is None:
            continue
        snaps = parser.parsePart(str(partPath), col=16)["connectors"]
        brickMaker.storeSnaps(mesh, snaps)
