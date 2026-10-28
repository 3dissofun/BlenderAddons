"""Screen-space snap matching for the interactive tools.

While bricks are being dragged, every snap on them is compared with every
compatible snap on the other visible bricks. For each compatible pair the
snapped position is worked out (see Joints and Cylinder fitting below), and
pairs whose snapped position is within SNAP_PX pixels of where the dragged
snap is now are candidates. Candidates are scored on screen distance and on
depth, so a snap nearer the camera beats one further back that happens to be at
a similar spot on screen (e.g. a stud on the brick in front of the mouse rather
than one on a brick hidden behind it). The best scoring pair wins and the drag
is corrected to that snapped position, tilting the dragged bricks if needed so
the two snaps line up (see Alignment).

Score (lower is better), with every term roughly 0..1:
    pixel distance / SNAP_PX
  + DEPTH_WEIGHT * min(extra depth / DEPTH_RANGE, 1)
  + ALIGN_WEIGHT * tilt / ALIGN_ANGLE
where extra depth is how much further from the view the snap is than the
nearest candidate, in the dragged brick's local units (LDU), and tilt is how
far the dragged bricks have to turn to line the two snaps up. So at a similar
spot on screen, a snap that's already lined up beats one that needs a tilt.

Data comes from Mesh.lego_snaps (see ldraw/snapUiProps.py): mesh local space,
Blender units, each snap pointing along its local -Y.

Joints
------
A cylinder's profile (secs) runs from its start (the origin, or half its length
back with center) along the axis, section after section. It is turned into
joints, each with a position, a direction and a profile:
- Female: one joint at each open end of the hole (caps=one closes the far end),
  pointing into the hole, with the hole's profile from that opening inwards.
- Male: one joint at each free (uncapped) end, its tip, pointing from the rest
  of the cylinder towards the tip, with the profile from the tip backwards.
A male tip joins a female opening when they point the same way: the male is
pushed into the hole tip first, along the hole's direction.

Other kinds get one joint at their origin and connect origin to origin when
their radii match (within RADIUS_TOL). Clips, fingers and spheres use their own
radius, and a male cylinder joining a clip uses its tip section's radius.
Generic snaps skip the radius check, as their radius is only a rough bounding
size and their group already says what they fit.

Cylinder fitting
----------------
A male tip and a female opening connect if the male can be pushed into the
hole and sits snugly once it's in. _fitDepth slides the tip in from the
opening until some part of the male would be wider than the hole around it
(a wider collar meeting the face of the hole, or the tip reaching a closed
end), and that depth is where the two come to rest. Outside the opening is free
space; past the far end of the hole is free space too, unless that end is
capped. Behind the male's last section is the rest of its part if that end is
capped (so it can't enter the hole), and nothing if not.
They connect only if, at that depth, some section of the male overlaps a
section of the hole of the same radius. So 6141's stud (R 6 4, then its
R 10 3 collar) goes 4 LDU into 62462's pin hole (R 8 2  R 6 16 ...): through
the R8 countersink, snug in the R6 bore, stopped by the collar at the face. A
male that never gets stopped (a bar through an open hole) rests with its back
end at the opening.

Snaps of different radii therefore never connect: a male narrower than the
hole everywhere it reaches isn't snug, and one wider than the opening can't go
in at all.

Alignment
---------
Two snaps can connect when their axes are within ALIGN_ANGLE of each other.
The dragged bricks are then turned by the smallest rotation that takes the
dragged snap's axis onto the other snap's axis, about the dragged snap. That
rotation only tilts the axis and never twists about it, so the bricks keep
their spin (which way they face around the snap). E.g. a glass held upright
tilts by 14.5 degrees to drop into a minifig hand's angled clip, but keeps
facing the way it did. Snaps further apart than ALIGN_ANGLE still need turning
with W/A/S/D first, so a brick never flips over just by passing near a snap
facing another way.

Performance
-----------
Whether two joints can connect (and how deep they fit) doesn't depend on where
the mouse is, only on what they are and which way they point. So joints are
grouped into classes (same kind, gender, group, profile or radius, and axis):
all 256 studs on a 16x16 plate are one class. Compatibility is worked out once
per pair of classes and cached for the whole move.

On each mouse move, for every compatible class pair, the static joints' snap
targets are projected and bucketed in a screen-space grid with cells SNAP_PX
wide, and each dragged joint only looks at the 3x3 cells around it. Cost per
move is roughly (static joints in compatible classes) + (pairs that are
actually close on screen), not (dragged joints) x (all joints in the scene).

Usage (see move.py):
    snapper = Snapper(context, targets)          # once, at the start of a move
    snap = snapper.find(context, base, delta)    # every update, may return None
    # snap is a world-space Matrix: snapped matrix = snap @ base[obj]
"""
import math

import numpy as np
from mathutils import Matrix

SNAP_PX = 60.0       # max screen distance between two snaps, in pixels (at UI scale 1)
DEPTH_WEIGHT = 1.0   # how much depth counts against screen distance in the score
DEPTH_RANGE = 160.0  # LDU of extra depth that earns the full depth penalty (8 studs)
RADIUS_TOL = 0.05    # LDU; radii closer than this count as the same size
ALIGN_ANGLE = math.radians(45.0)  # max angle between two snap axes that still snap
ALIGN_WEIGHT = 0.5   # how much the tilt needed counts in the score
ALIGN_DOT = math.cos(ALIGN_ANGLE)

INF = float("inf")

KINDS = ("CYL", "CLP", "FGR", "GEN", "SPH")
_K = {k: i for i, k in enumerate(KINDS)}
CYL = _K["CYL"]

# KIND_OK[a, b]: can a snap of kind a connect to one of kind b
KIND_OK = np.zeros((len(KINDS), len(KINDS)), dtype=bool)
for a, b in (("CYL", "CYL"), ("CYL", "CLP"), ("CLP", "CYL"),
             ("FGR", "FGR"), ("GEN", "GEN"), ("SPH", "SPH")):
    KIND_OK[_K[a], _K[b]] = True

# Kinds where gender doesn't have to be opposite (fingers alternate along the hinge)
ANY_GENDER = np.zeros(len(KINDS), dtype=bool)
ANY_GENDER[_K["FGR"]] = True

# Kinds whose radius is only a rough bounding size, so it isn't compared
ANY_RADIUS = np.zeros(len(KINDS), dtype=bool)
ANY_RADIUS[_K["GEN"]] = True


# ---------------------------------------------------------------------------
# Joints
# ---------------------------------------------------------------------------

def _joints(snap):
    """(offset along the axis, direction +1/-1, radius, profile) per joint.

    profile is None for non-cylinders, else (sections, closedBeyond):
    sections ((radius, length), ...) from the joint inwards (female) or from the
    tip backwards (male), and closedBeyond whether the far end of the profile is
    capped. radius is the section at the joint. Local units; see Joints above.
    """
    secs = tuple((sec.radius, sec.length) for sec in snap.secs) if snap.kind == 'CYL' else ()
    if not secs:
        return [(0.0, 1, snap.radius, None)]

    total = sum(l for _, l in secs)
    start = -total / 2 if snap.center else 0.0
    end = start + total
    rev = secs[::-1]

    out = []
    if snap.gender == 'F':
        if not snap.capA:  # opening at the start, hole runs along +axis
            out.append((start, 1, secs[0][0], (secs, snap.capB)))
        if not snap.capB:  # opening at the far end, hole runs back along -axis
            out.append((end, -1, rev[0][0], (rev, snap.capA)))
    else:
        if not snap.capB:  # tip at the far end, pushed in along +axis
            out.append((end, 1, rev[0][0], (rev, snap.capA)))
        if not snap.capA:  # tip at the start, pushed in along -axis
            out.append((start, -1, secs[0][0], (secs, snap.capB)))
    return out


def _fitDepth(male, female, tol):
    """How far a male tip goes past a female opening, or None if they don't connect.

    male, female: (sections, closedBeyond) profiles from _joints, scaled to the
    same units as tol. See Cylinder fitting above.
    """
    mSecs, mBackClosed = male
    fSecs, fFarClosed = female

    # Male segments (radius, from, to) as distances behind the tip
    m, b = [], 0.0
    for r, l in mSecs:
        m.append((r, b, b + l))
        b += l
    mLen = b
    if mBackClosed:
        m.append((INF, mLen, INF))  # the rest of the part behind a capped end

    # Female segments (radius, from, to) as depths past the opening
    f, c = [(INF, -INF, 0.0)], 0.0  # free space outside the opening
    for r, l in fSecs:
        f.append((r, c, c + l))
        c += l
    f.append((0.0 if fFarClosed else INF, c, INF))

    # With the tip at depth x, male segment (r, b0, b1) covers depths x-b1..x-b0.
    # Where it is wider than female segment (fr, c0, c1), x can't be in
    # (c0 + b0, c1 + b1); the tip stops at the first such limit past the opening.
    x = INF
    for r, b0, b1 in m:
        for fr, c0, c1 in f:
            if r > fr + tol and c0 + b0 >= 0.0:
                x = min(x, c0 + b0)
    if x == INF:
        x = mLen  # never stopped: back end of the male at the opening
    if x <= tol:
        return None  # can't get in at all

    # Snug: some male section overlaps a hole section of the same radius
    for r, b0, b1 in m:
        for fr, c0, c1 in f:
            if abs(r - fr) <= tol and min(x - b0, c1) - max(x - b1, c0) > tol:
                return x
    return None


def hasSnaps(obj):
    return (obj.type == 'MESH' and getattr(obj, "is_lego", False)
            and len(getattr(obj.data, "lego_snaps", ())) > 0)


def _scale(matrix):
    return float(np.linalg.norm(matrix[:3, 0]))  # world size of one local unit


class _Local:
    """One mesh's snap joints as arrays, in mesh local space (one row per joint)."""

    def __init__(self, mesh, groupIds):
        coll = mesh.lego_snaps
        n = len(coll)
        flat = np.empty(n * 16, dtype=np.float32)
        coll.foreach_get("matrix", flat)
        mats = flat.reshape(n, 4, 4).astype(np.float64)  # row-major, like snapUi

        pos, axis, kind, male, radius, group, self.profiles = [], [], [], [], [], [], []
        for m, s in zip(mats, coll):
            origin, snapAxis = m[:3, 3], -m[:3, 1]
            g = groupIds.setdefault(s.group.lower(), len(groupIds))
            for offset, direction, r, profile in _joints(s):
                pos.append(origin + snapAxis * offset)
                axis.append(snapAxis * direction)
                kind.append(_K[s.kind])
                male.append(s.gender == 'M')
                radius.append(r)
                group.append(g)
                self.profiles.append(profile)

        self.pos = np.array(pos, dtype=np.float64).reshape(-1, 3)
        self.axis = np.array(axis, dtype=np.float64).reshape(-1, 3)
        self.kind = np.array(kind, dtype=np.int8)
        self.male = np.array(male, dtype=bool)
        self.radius = np.array(radius, dtype=np.float64)  # local units
        self.group = np.array(group, dtype=np.int32)

    def world(self, matrix):
        """(positions, unit axes) under a 4x4 numpy world matrix."""
        R, t = matrix[:3, :3], matrix[:3, 3]
        pos = self.pos @ R.T + t
        axis = self.axis @ R.T
        axis /= np.maximum(np.linalg.norm(axis, axis=1, keepdims=True), 1e-12)
        return pos, axis


# ---------------------------------------------------------------------------
# View helpers
# ---------------------------------------------------------------------------

def _project(context, points):
    """World points -> region pixel coords, plus a mask of points in front of the view."""
    region, rv3d = context.region, context.region_data
    persp = np.array(rv3d.perspective_matrix, dtype=np.float64)
    h = np.c_[points, np.ones(len(points))] @ persp.T
    w = h[:, 3]
    front = w > 1e-6
    ndc = h[:, :2] / np.where(front, w, 1.0)[:, None]
    px = (ndc * 0.5 + 0.5) * np.array([region.width, region.height])
    return px, front


def _viewDepth(context, points):
    """Distance in front of the view plane (works for perspective and ortho)."""
    view = np.array(context.region_data.view_matrix, dtype=np.float64)
    return -(points @ view[2, :3] + view[2, 3])


# ---------------------------------------------------------------------------
# Screen-space neighbour search
# ---------------------------------------------------------------------------

_KEY = 1 << 21  # grid cells per row in a key; plenty once points are kept near the region

def _nearPairs(a, b, r):
    """Index pairs (ia, ib) and distances of points a[ia], b[ib] closer than r.

    a, b: (n, 2) / (m, 2) pixel coords, already limited to around the region.
    Buckets b in a grid of r-wide cells, then checks each a against its 3x3 cells.
    """
    empty = (np.empty(0, np.int64), np.empty(0, np.int64), np.empty(0))
    if len(a) == 0 or len(b) == 0:
        return empty
    cb = np.floor(b / r).astype(np.int64)
    keyB = cb[:, 0] * _KEY + cb[:, 1]
    order = np.argsort(keyB, kind="stable")
    sortedB = keyB[order]
    ca = np.floor(a / r).astype(np.int64)

    ias, ibs = [], []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            k = (ca[:, 0] + dx) * _KEY + (ca[:, 1] + dy)
            lo = np.searchsorted(sortedB, k, "left")
            cnt = np.searchsorted(sortedB, k, "right") - lo
            total = int(cnt.sum())
            if total == 0:
                continue
            ia = np.repeat(np.arange(len(a)), cnt)
            within = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
            ias.append(ia)
            ibs.append(order[np.repeat(lo, cnt) + within])
    if not ias:
        return empty
    ia, ib = np.concatenate(ias), np.concatenate(ibs)
    dist = np.linalg.norm(a[ia] - b[ib], axis=1)
    near = dist < r
    return ia[near], ib[near], dist[near]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _classKeys(kind, male, group, prof, radius, axis):
    # One integer row per joint; joints with equal rows behave identically.
    # Radius (world units) and axis are rounded to 1/1000.
    return np.column_stack([kind.astype(np.int64), male.astype(np.int64),
                            group.astype(np.int64), prof.astype(np.int64),
                            np.round(radius * 1000).astype(np.int64),
                            np.round(axis * 1000).astype(np.int64)])


def _classes(keys):
    """(unique key tuples, member index arrays) for a block of class keys."""
    if len(keys) == 0:
        return [], []
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = inv.ravel()
    order = np.argsort(inv, kind="stable")
    bounds = np.cumsum(np.bincount(inv, minlength=len(uniq)))[:-1]
    return [tuple(int(v) for v in k) for k in uniq], np.split(order, bounds)


class Snapper:
    def __init__(self, context, targets):
        self._groups = {"": 0}
        self._cache = {}       # mesh -> _Local; bricks of one part share one mesh
        self._profIds = {}     # (mesh, scale) -> profile id per joint
        self._profiles = {}    # world-scaled profile -> id
        self._byId = []        # id -> world-scaled profile
        self._fits = {}        # (male profile id, female profile id) -> depth or None
        self._compat = {}      # moving class key -> [(static class, offset along its axis)]

        # Fits compare radii in world units; the tolerance follows the dragged bricks
        self.tol = RADIUS_TOL * (_scale(np.array(targets[0].matrix_world)) if targets else 1.0)

        self.moving = []
        for obj in targets:
            if hasSnaps(obj):
                local = self._local(obj.data)
                self.moving.append((obj, local, self._profileIds(obj.data, local,
                                                                 _scale(np.array(obj.matrix_world)))))

        moving = set(targets)
        pos, axis, kind, male, group, radius, prof = [], [], [], [], [], [], []
        for obj in context.visible_objects:
            if obj in moving or not hasSnaps(obj):
                continue
            local = self._local(obj.data)
            mw = np.array(obj.matrix_world, dtype=np.float64)
            sc = _scale(mw)
            p, a = local.world(mw)
            pos.append(p)
            axis.append(a)
            kind.append(local.kind)
            male.append(local.male)
            group.append(local.group)
            # Radii and profiles in world units, so bricks at different scales compare
            radius.append(local.radius * sc)
            prof.append(self._profileIds(obj.data, local, sc))

        self.active = bool(self.moving and pos and any(len(l.pos) for _, l, _ in self.moving))
        if not self.active:
            return
        self.pos = np.concatenate(pos)
        self.axis = np.concatenate(axis)
        self.active = len(self.pos) > 0
        keys = _classKeys(np.concatenate(kind), np.concatenate(male), np.concatenate(group),
                          np.concatenate(prof), np.concatenate(radius), self.axis)
        self.classKeys, self.members = _classes(keys)

    def _local(self, mesh):
        local = self._cache.get(mesh)
        if local is None:
            local = self._cache[mesh] = _Local(mesh, self._groups)
        return local

    def _profileIds(self, mesh, local, scale):
        # One id per distinct world-scaled profile (-1 for non-cylinders), so
        # each male/female combination is only ever fitted once
        cacheKey = (mesh, round(scale, 6))
        ids = self._profIds.get(cacheKey)
        if ids is not None:
            return ids
        out = []
        for p in local.profiles:
            if p is None:
                out.append(-1)
                continue
            key = (tuple((round(r * scale, 4), round(l * scale, 4)) for r, l in p[0]), p[1])
            pid = self._profiles.get(key)
            if pid is None:
                pid = self._profiles[key] = len(self._byId)
                self._byId.append(key)
            out.append(pid)
        ids = self._profIds[cacheKey] = np.array(out, dtype=np.int64)
        return ids

    def _depth(self, maleId, femaleId):
        key = (maleId, femaleId)
        if key not in self._fits:
            self._fits[key] = _fitDepth(self._byId[maleId], self._byId[femaleId], self.tol)
        return self._fits[key]

    def _offset(self, ka, kb):
        """How far along static class kb's axis a moving class ka joint snaps
        from kb's joints, or None if the two classes can't connect."""
        kA, mA, gA, pA, rA = ka[:5]
        kB, mB, gB, pB, rB = kb[:5]
        if not KIND_OK[kA, kB] or gA != gB:
            return None
        if not ANY_GENDER[kA] and mA == mB:
            return None
        axA, axB = np.array(ka[5:], float), np.array(kb[5:], float)
        if axA @ axB <= ALIGN_DOT * np.linalg.norm(axA) * np.linalg.norm(axB):
            return None
        if pA >= 0 and pB >= 0:
            # Cylinder to cylinder: fit the profiles, then push the male in that far
            x = self._depth(pA, pB) if mA else self._depth(pB, pA)
            if x is None:
                return None
            return x if mA else -x
        # Anything else: origin to origin, radii must match
        if not ANY_RADIUS[kA] and abs(rA - rB) / 1000.0 > self.tol + 1e-3:
            return None
        return 0.0

    def _compatible(self, ka):
        out = self._compat.get(ka)
        if out is None:
            out = []
            for b, kb in enumerate(self.classKeys):
                off = self._offset(ka, kb)
                if off is not None:
                    out.append((b, off))
            self._compat[ka] = out
        return out

    def find(self, context, base, delta, maxPx=None):
        """World transform that snaps this drag, or None if nothing is close enough.

        base:  {obj: world Matrix} the bricks are being translated from
        delta: the free (unsnapped) translation, a mathutils Vector
        Returns a Matrix to left-multiply onto each base matrix (snapped
        matrix = result @ base[obj]): the alignment tilt about the dragged
        snap, then the move that lands it on the target.
        """
        if not self.active:
            return None
        if maxPx is None:
            maxPx = SNAP_PX * context.preferences.system.ui_scale
        region = context.region
        lo = np.array([-maxPx, -maxPx])
        hi = np.array([region.width + maxPx, region.height + maxPx])

        def onScreen(px, front):
            return front & np.all((px > lo) & (px < hi), axis=1)

        d = np.array(delta, dtype=np.float64)
        targets = {}  # (static class, offset) -> (positions, axes, px), this update

        # Candidates: (pixel distance, depth, target, pivot, moving axis, static axis, LDU size)
        cand = []
        for obj, local, prof in self.moving:
            if len(local.pos) == 0:
                continue
            matrix = np.array(base[obj], dtype=np.float64)
            pos, axis = local.world(matrix)
            ldu = _scale(matrix)
            freePx, freeFront = _project(context, pos + d)
            visible = onScreen(freePx, freeFront)

            keys, members = _classes(_classKeys(local.kind, local.male, local.group,
                                                prof, local.radius * ldu, axis))
            for ka, ia in zip(keys, members):
                ia = ia[visible[ia]]
                if len(ia) == 0:
                    continue
                for b, off in self._compatible(ka):
                    t = targets.get((b, off))
                    if t is None:
                        jb = self.members[b]
                        tPos = self.pos[jb] + off * self.axis[jb]
                        tPx, tFront = _project(context, tPos)
                        keep = onScreen(tPx, tFront)
                        t = targets[(b, off)] = (tPos[keep], self.axis[jb][keep], tPx[keep])
                    tPos, tAxis, tPx = t
                    i, j, dist = _nearPairs(freePx[ia], tPx, maxPx)
                    if len(i) == 0:
                        continue
                    snapped = tPos[j]
                    cand.append((dist, _viewDepth(context, snapped), snapped,
                                 pos[ia[i]], axis[ia[i]], tAxis[j], np.full(len(i), ldu)))

        if not cand:
            return None
        px, depth, target, pivot, mAxis, sAxis, ldu = (np.concatenate(c) for c in zip(*cand))

        tilt = np.arccos(np.clip((mAxis * sAxis).sum(axis=1), -1.0, 1.0))
        extra = (depth - depth.min()) / np.maximum(ldu * DEPTH_RANGE, 1e-12)
        score = (px / maxPx
                 + DEPTH_WEIGHT * np.minimum(extra, 1.0)
                 + ALIGN_WEIGHT * tilt / ALIGN_ANGLE)
        k = int(np.argmin(score))
        return _snapMatrix(pivot[k], mAxis[k], target[k], sAxis[k])


def _swing(a, b):
    """Smallest rotation (3x3) taking unit vector a onto unit vector b.

    It turns about a x b only, so nothing spins about the axis itself.
    """
    c = float(a @ b)
    v = np.cross(a, b)
    s2 = float(v @ v)
    if s2 < 1e-16:
        return np.eye(3)  # already lined up (opposite never gets past ALIGN_ANGLE)
    vx = np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])
    return np.eye(3) + vx + vx @ vx * ((1.0 - c) / s2)


def _snapMatrix(pivot, movingAxis, target, staticAxis):
    # Tilt about the dragged joint so its axis matches, then move it onto the target
    m = np.eye(4)
    R = _swing(movingAxis, staticAxis)
    m[:3, :3] = R
    m[:3, 3] = target - R @ pivot
    return Matrix(m.tolist())
