"""Screen-space snap matching for the interactive tools.

While bricks are being dragged, every snap on them is compared with every
compatible snap on the other visible bricks. Both are projected to the screen
and every pair that lands within SNAP_PX pixels is a candidate. Candidates
are scored on screen distance and on depth, so a snap nearer the camera beats
one further back that happens to be at a similar spot on screen (e.g. a stud on
the brick in front of the mouse rather than one on a brick hidden behind it).
The best scoring pair wins and the drag is corrected so those two snaps
coincide in 3D.

Score (lower is better), with both terms roughly 0..1:
    pixel distance / SNAP_PX  +  DEPTH_WEIGHT * min(extra depth / DEPTH_RANGE, 1)
where extra depth is how much further from the view the snap is than the
nearest candidate, in the dragged brick's local units (LDU).

Data comes from Mesh.lego_snaps (see ldraw/snapUiProps.py): mesh local space,
Blender units, each snap pointing along its local -Y. Two snaps connect when
their origins meet and their axes point the same way (e.g. a stud's base and
the anti-stud on the underside of the brick above it, both pointing up).

Orientation is never changed here: rotate with W/A/S/D during the move and
only snaps whose axes already line up are considered.

Usage (see move.py):
    snapper = Snapper(context, targets)          # once, at the start of a move
    delta = snapper.find(context, base, delta)   # every update, may return None
"""
import numpy as np
from mathutils import Vector

SNAP_PX = 60.0       # max screen distance between two snaps, in pixels (at UI scale 1)
DEPTH_WEIGHT = 1.0   # how much depth counts against screen distance in the score
DEPTH_RANGE = 160.0  # LDU of extra depth that earns the full depth penalty (8 studs)
AXIS_DOT = 0.985     # cos of the max angle between two snap axes (~10 degrees)

KINDS = ("CYL", "CLP", "FGR", "GEN", "SPH")
_K = {k: i for i, k in enumerate(KINDS)}

# KIND_OK[a, b]: can a snap of kind a connect to one of kind b
KIND_OK = np.zeros((len(KINDS), len(KINDS)), dtype=bool)
for a, b in (("CYL", "CYL"), ("CYL", "CLP"), ("CLP", "CYL"),
             ("FGR", "FGR"), ("GEN", "GEN"), ("SPH", "SPH")):
    KIND_OK[_K[a], _K[b]] = True

# Kinds where gender doesn't have to be opposite (fingers alternate along the hinge)
ANY_GENDER = np.zeros(len(KINDS), dtype=bool)
ANY_GENDER[_K["FGR"]] = True


def hasSnaps(obj):
    return (obj.type == 'MESH' and getattr(obj, "is_lego", False)
            and len(getattr(obj.data, "lego_snaps", ())) > 0)


class _Local:
    """One mesh's snaps as arrays, in mesh local space."""

    def __init__(self, mesh, groupIds):
        coll = mesh.lego_snaps
        n = len(coll)
        flat = np.empty(n * 16, dtype=np.float32)
        coll.foreach_get("matrix", flat)
        mats = flat.reshape(n, 4, 4).astype(np.float64)  # row-major, like snapUi
        self.pos = mats[:, :3, 3].copy()
        self.axis = -mats[:, :3, 1]
        self.kind = np.array([_K[s.kind] for s in coll], dtype=np.int8)
        self.male = np.array([s.gender == 'M' for s in coll], dtype=bool)
        self.group = np.array([groupIds.setdefault(s.group.lower(), len(groupIds))
                               for s in coll], dtype=np.int32)

    def world(self, matrix):
        """(positions, unit axes) under a 4x4 numpy world matrix."""
        R, t = matrix[:3, :3], matrix[:3, 3]
        pos = self.pos @ R.T + t
        axis = self.axis @ R.T
        axis /= np.maximum(np.linalg.norm(axis, axis=1, keepdims=True), 1e-12)
        return pos, axis


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


class Snapper:
    def __init__(self, context, targets):
        self._groups = {"": 0}
        self._cache = {}  # mesh -> _Local; bricks of one part share one mesh

        self.moving = [(obj, self._local(obj.data)) for obj in targets if hasSnaps(obj)]

        moving = set(targets)
        pos, axis, kind, male, group = [], [], [], [], []
        for obj in context.visible_objects:
            if obj in moving or not hasSnaps(obj):
                continue
            local = self._local(obj.data)
            p, a = local.world(np.array(obj.matrix_world, dtype=np.float64))
            pos.append(p)
            axis.append(a)
            kind.append(local.kind)
            male.append(local.male)
            group.append(local.group)

        self.active = bool(self.moving and pos)
        if self.active:
            self.pos = np.concatenate(pos)
            self.axis = np.concatenate(axis)
            self.kind = np.concatenate(kind)
            self.male = np.concatenate(male)
            self.group = np.concatenate(group)

    def _local(self, mesh):
        local = self._cache.get(mesh)
        if local is None:
            local = self._cache[mesh] = _Local(mesh, self._groups)
        return local

    def find(self, context, base, delta, maxPx=None):
        """Snapped translation for this drag, or None if nothing is close enough.

        base:  {obj: world Matrix} the bricks are being translated from
        delta: the free (unsnapped) translation, a mathutils Vector
        """
        if not self.active:
            return None
        if maxPx is None:
            maxPx = SNAP_PX * context.preferences.system.ui_scale

        staticPx, staticFront = _project(context, self.pos)
        staticDepth = _viewDepth(context, self.pos)
        d = np.array(delta, dtype=np.float64)

        # Gather every pair within maxPx: (pixel distance, depth, delta, world LDU size)
        cand = []
        for obj, local in self.moving:
            matrix = np.array(base[obj], dtype=np.float64)
            pos, axis = local.world(matrix)
            movedPx, movedFront = _project(context, pos + d)

            # (moving snaps x static snaps) compatibility
            ok = KIND_OK[local.kind[:, None], self.kind[None, :]]
            ok &= ANY_GENDER[local.kind][:, None] | (local.male[:, None] != self.male[None, :])
            ok &= local.group[:, None] == self.group[None, :]
            ok &= (axis @ self.axis.T) > AXIS_DOT
            ok &= movedFront[:, None] & staticFront[None, :]
            if not ok.any():
                continue

            d2 = ((movedPx[:, None, :] - staticPx[None, :, :]) ** 2).sum(axis=2)
            ok &= d2 < maxPx * maxPx
            i, j = np.nonzero(ok)
            if len(i) == 0:
                continue
            ldu = np.linalg.norm(matrix[:3, 0])  # world size of one local unit
            cand.append((np.sqrt(d2[i, j]), staticDepth[j],
                         self.pos[j] - pos[i],  # delta that puts snap i on snap j
                         np.full(len(i), ldu)))

        if not cand:
            return None
        px, depth, deltas, ldu = (np.concatenate(c) for c in zip(*cand))

        extra = (depth - depth.min()) / np.maximum(ldu * DEPTH_RANGE, 1e-12)
        score = px / maxPx + DEPTH_WEIGHT * np.minimum(extra, 1.0)
        return Vector(deltas[np.argmin(score)].tolist())
