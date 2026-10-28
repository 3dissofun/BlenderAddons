"""LDCad shadow library snap info (0 !LDCAD SNAP_* metas).

Snaps come out in LDraw units, in the space of the top-level part being parsed.
The root matrix passed to parsePart (e.g. LDraw -> Blender with scaling) is
divided back out, so the scale/mirror inheritance rules see true LDraw scale.
Converting to Blender space is the importer's job.

Conventions (from the LDCad meta reference):
- Every snap points along its local -Y axis when at rest.
- A shadow file's metas are applied after its subfiles' snaps are collected,
  so SNAP_CLEAR only removes inherited info, never the file's own snaps.
- SNAP_INCL copies only the snap metas written in the referenced shadow file.
  Nested includes inside that file are not followed.
"""
import re
from dataclasses import dataclass, field, replace
from functools import lru_cache

import numpy as np

from . import library

SNAP_KINDS = ("SNAP_CYL", "SNAP_CLP", "SNAP_FGR", "SNAP_GEN", "SNAP_SPH")
SEC_SHAPES = ("R", "A", "S", "_L", "L_")
SCALE_TOL = 1e-4   # scale / mirror detection
DEDUPE_TOL = 1e-3  # LDU

_META = re.compile(r"^\s*0\s+!LDCAD\s+(\S+)(.*)$")
_ARG = re.compile(r"\[\s*([^=\]\s]+)\s*=([^\]]*)\]")
_KNOWN = {"id", "group", "pos", "ori", "scale", "mirror", "gender", "secs", "caps",
          "grid", "center", "slide", "radius", "length", "genderofs", "seq",
          "bounding", "ref"}

@dataclass(frozen=True, eq=False)
class Snap:
    kind: str                     # one of SNAP_KINDS
    matrix: np.ndarray            # 4x4, rotation + translation only
    gender: str = "M"             # "M" or "F" (fingers: gender of the first finger)
    group: str = ""
    ids: frozenset = frozenset()  # lowercased; own ID plus any enclosing include ID
    secs: tuple = ()              # ((shape, radius, length), ...) from pos along -Y.
                                  # Fingers: shape is "M"/"F" per finger, length = width
    capA: bool = False            # end at pos closed
    capB: bool = False            # far end closed
    center: bool = False          # profile centred on pos instead of starting at it
    slide: bool = False
    radius: float = 0.0           # CYL: largest section radius; CLP/FGR/SPH: own radius
    length: float = 0.0           # total length along the axis
    bounding: tuple = ()          # GEN bounding tokens, LDU, unscaled
    scaleRule: str = "none"       # none / yonly / ronly / yandr
    mirrorRule: str = "none"      # none / cor
    extra: dict = field(default_factory=dict)  # unknown params, kept raw

# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

def _params(text):
    return {k.lower(): v.strip() for k, v in _ARG.findall(text)}

def _floats(text, n, default):
    try:
        vals = [float(v) for v in (text or "").split()]
    except ValueError:
        return default
    return vals if len(vals) == n else default

def _float(text, default):
    vals = _floats(text, 1, None)
    return vals[0] if vals else default

def _bool(text):
    return (text or "").strip().lower() == "true"

def _gender(text, default="M"):
    t = (text or "").strip().upper()
    if t in ("M", "MALE"):
        return "M"
    if t in ("F", "FEMALE"):
        return "F"
    return default

def _caps(text, gender):
    # Returns (capA, capB). A = end at pos, B = far end along -Y.
    t = (text or "one").strip().lower()
    if t == "none":
        return False, False
    if t == "two":
        return True, True
    if t == "a":
        return True, False
    if t == "b":
        return False, True
    return (True, False) if gender == "M" else (False, True)  # "one"

def _secs(text):
    toks = (text or "").split()
    out = []
    for i in range(0, len(toks) - len(toks) % 3, 3):
        shape = toks[i].upper()
        if shape not in SEC_SHAPES:
            return ()
        try:
            out.append((shape, float(toks[i + 1]), float(toks[i + 2])))
        except ValueError:
            return ()
    return tuple(out)

def _grid(text):
    # "[C] Xcnt [C] Zcnt Xstep Zstep" -> list of local (x, 0, z) offsets
    if not text:
        return [np.zeros(3)]
    toks = text.split()
    try:
        i, counts, centred = 0, [], []
        for _ in range(2):
            c = toks[i].upper() == "C"
            i += c
            counts.append(int(float(toks[i])))
            centred.append(c)
            i += 1
        steps = (float(toks[i]), float(toks[i + 1]))
    except (IndexError, ValueError):
        print(f"LDRAW PARSER: WARNING, Malformed snap grid '{text}'")
        return [np.zeros(3)]

    axes = []
    for n, step, c in zip(counts, steps, centred):
        start = -(n - 1) * step / 2 if c else 0.0
        axes.append([start + k * step for k in range(max(n, 1))])
    return [np.array([x, 0.0, z]) for x in axes[0] for z in axes[1]]

def _localMatrices(p, scale=None):
    # T(pos) @ R(ori) @ T(gridOffset) [@ S(scale)] for every grid instance
    base = np.eye(4)
    base[:3, 3] = _floats(p.get("pos"), 3, [0.0, 0.0, 0.0])
    base[:3, :3] = np.array(_floats(p.get("ori"), 9, [1, 0, 0, 0, 1, 0, 0, 0, 1])).reshape(3, 3)
    out = []
    for off in _grid(p.get("grid")):
        m = base.copy()
        m[:3, 3] = base[:3, :3] @ off + base[:3, 3]
        if scale is not None:
            m[:3, :3] = m[:3, :3] * scale  # scales columns, i.e. R @ diag(scale)
        out.append(m)
    return out

def _boundingRadius(tokens):
    # A rough size for drawing / candidate ranking
    try:
        vals = [float(v) for v in tokens[1:]]
    except ValueError:
        return 0.0
    kind = tokens[0].lower() if tokens else "pnt"
    if kind in ("sph", "cube", "cyl") and vals:
        return vals[0]
    if kind == "box" and len(vals) >= 3:
        return max(vals[0], vals[2])
    return 0.0

def _buildSnap(kind, p, local):
    gender = "F" if kind == "SNAP_CLP" else _gender(p.get("gender"), "M")
    common = dict(
        kind=kind,
        matrix=local,
        gender=gender,
        group=p.get("group", ""),
        ids=frozenset({p["id"].lower()}) if p.get("id") else frozenset(),
        center=_bool(p.get("center")),
        slide=_bool(p.get("slide")),
        scaleRule=p.get("scale", "none").lower(),
        mirrorRule=p.get("mirror", "cor" if kind == "SNAP_CYL" else "none").lower(),
        extra={k: v for k, v in p.items() if k not in _KNOWN},
    )

    if kind == "SNAP_CYL":
        secs = _secs(p.get("secs"))
        if not secs:
            print(f"LDRAW PARSER: WARNING, SNAP_CYL with missing/invalid secs '{p.get('secs')}'")
            return None
        capA, capB = _caps(p.get("caps"), gender)
        return Snap(secs=secs, capA=capA, capB=capB,
                    radius=max(r for _, r, _ in secs),
                    length=sum(l for _, _, l in secs), **common)

    if kind == "SNAP_CLP":
        return Snap(radius=_float(p.get("radius"), 4.0),
                    length=_float(p.get("length"), 8.0), **common)

    if kind == "SNAP_FGR":
        common["gender"] = _gender(p.get("genderofs"), "M")
        try:
            seq = [float(v) for v in p.get("seq", "").split()]
        except ValueError:
            seq = []
        if not seq:
            print("LDRAW PARSER: WARNING, SNAP_FGR without a valid seq")
            return None
        radius = _float(p.get("radius"), 0.0)
        other = "F" if common["gender"] == "M" else "M"
        secs = tuple((common["gender"] if i % 2 == 0 else other, radius, w)
                     for i, w in enumerate(seq))
        return Snap(secs=secs, radius=radius, length=sum(seq), **common)

    if kind == "SNAP_GEN":
        bounding = tuple(p.get("bounding", "pnt").split())
        return Snap(bounding=bounding, radius=_boundingRadius(bounding), **common)

    if kind == "SNAP_SPH":
        return Snap(radius=_float(p.get("radius"), 0.0), **common)

    return None

# ---------------------------------------------------------------------------
# Transforming with LDCad's inheritance rules
# ---------------------------------------------------------------------------

def _transform(snap, M, enforce=True):
    # Move a snap by M. With enforce, apply its scale/mirror inheritance rules
    # (dropping it if they forbid the reference). Scale is folded into the
    # numeric fields; the returned matrix is always rotation + translation.
    C = M[:3, :3] @ snap.matrix[:3, :3]       # snap axes after the transform
    sc = np.linalg.norm(C, axis=0)            # scale along snap X, Y, Z
    if np.any(sc < 1e-9):
        return None
    sx, sy, sz = sc
    mirrored = np.linalg.det(C) < 0
    scaled = bool(np.any(np.abs(sc - 1) > SCALE_TOL))

    if enforce:
        if mirrored and snap.mirrorRule != "cor":
            return None
        if scaled:
            yOnly = abs(sx - 1) <= SCALE_TOL and abs(sz - 1) <= SCALE_TOL
            rOnly = abs(sx - sz) <= SCALE_TOL and abs(sy - 1) <= SCALE_TOL
            rule = snap.scaleRule
            if not ((rule == "yonly" and yOnly) or (rule == "ronly" and rOnly)
                    or (rule == "yandr" and (yOnly or rOnly))):
                return None

    rot = C / sc
    if np.linalg.det(rot) < 0:
        rot[:, 0] *= -1                       # "cor": flip one radius axis
    out = np.eye(4)
    out[:3, :3] = rot
    out[:3, 3] = M[:3, :3] @ snap.matrix[:3, 3] + M[:3, 3]

    if not scaled:
        return replace(snap, matrix=out)
    rS, yS = (sx + sz) / 2, sy
    return replace(snap, matrix=out,
                   secs=tuple((s, r * rS, l * yS) for s, r, l in snap.secs),
                   radius=snap.radius * rS, length=snap.length * yS)

# ---------------------------------------------------------------------------
# Shadow files
# ---------------------------------------------------------------------------

def _include(p, ownerPath):
    ref = p.get("ref", "")
    refPath = library.resolveShadowRef(ref) if ref else None
    if refPath is None:
        print(f"LDRAW PARSER: WARNING, SNAP_INCL ref '{ref}' not found in shadow library ({ownerPath})")
        return []

    scale = _floats(p.get("scale"), 3, None)
    scale = np.array(scale) if scale else None
    inclId = p["id"].lower() if p.get("id") else None

    out = []
    included = [d for k, d in readShadow(str(refPath), False) if k == "snap"]
    for local in _localMatrices(p, scale):
        for s in included:
            t = _transform(s, local, enforce=False)  # include scale is deliberate
            if t is not None:
                out.append(replace(t, ids=t.ids | {inclId}) if inclId else t)
    return out

@lru_cache(maxsize=None)
def readShadow(path, followIncludes=True):
    # Ordered records for one shadow file, in that file's local space:
    #   ("clear", id)   id "" means clear everything inherited
    #   ("snap", Snap)  grids expanded, includes resolved into snaps
    records = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            m = _META.match(line)
            if not m:
                continue
            cmd, p = m.group(1).upper(), _params(m.group(2))
            if cmd == "SNAP_CLEAR":
                records.append(("clear", p.get("id", "").lower()))
            elif cmd == "SNAP_INCL":
                if followIncludes:
                    records.extend(("snap", s) for s in _include(p, path))
                else:
                    print(f"LDRAW PARSER: NOTE, nested SNAP_INCL in '{path}' not followed")
            elif cmd in SNAP_KINDS:
                for local in _localMatrices(p):
                    s = _buildSnap(cmd, p, local)
                    if s is not None:
                        records.append(("snap", s))
    return tuple(records)

def applyShadow(file, matrix, output, start):
    # Apply one LDraw file's shadow info to output["connectors"]. Call after the
    # file's subfiles have been parsed: start is the connector count when this
    # file began, so connectors[start:] are the snaps it inherited.
    path = library.resolveShadowPart(file)
    if path is None:
        return
    rel = output["_snapRoot"] @ matrix       # this file -> top-level part, in LDU
    conns = output["connectors"]
    own = []
    for kind, data in readShadow(str(path)):
        if kind == "clear":
            if data:
                conns[start:] = [s for s in conns[start:] if data not in s.ids]
            else:
                del conns[start:]
        else:
            s = _transform(data, rel)
            if s is not None:
                own.append(s)
    conns.extend(own)

def dedupe(snaps, tol=DEDUPE_TOL):
    # Drop snaps that describe the same physical connection twice (e.g. a part
    # whose shadow includes a subpart's info while also using that subpart).
    # Same kind/gender/group/shape, same position and axis; rotation about the
    # axis is ignored.
    buckets, out = {}, []
    for s in snaps:
        pos = s.matrix[:3, 3]
        key = (s.kind, s.gender, s.group,
               tuple((sh, round(r, 3), round(l, 3)) for sh, r, l in s.secs),
               round(s.radius, 3), round(s.length, 3),
               tuple(np.round(pos, 2)))
        prev = buckets.setdefault(key, [])
        if any(np.allclose(pos, o.matrix[:3, 3], atol=tol)
               and np.allclose(s.matrix[:3, 1], o.matrix[:3, 1], atol=tol) for o in prev):
            continue
        prev.append(s)
        out.append(s)
    return out

def clearCaches():
    readShadow.cache_clear()
