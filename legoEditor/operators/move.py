"""
The intent of this file is to provide an override to the move operator (G) when in a lego point cloud.

Point positions and the brick transform maths are described in common.py.

Snapping modes (scene.lego_snap_mode, read when G is pressed):
    GRID   round the move to Grid Size LDU in the cloud's local space, so one step in the world
           is grid * object scale
    BRICK  connect the moving bricks' snaps (studs, holes, clips...) to the bricks around them,
           with the same matching as the brick move tool (tools/snapmatch.py). Snap data comes
           from each type's prototype mesh (Mesh.lego_snaps, built on demand). This may tilt
           the bricks to line the snaps up. When no snap is close it falls back to GRID
    NONE   move freely

Controls while moving:
    mouse          move
    X / Y / Z      lock to a world axis (press again to unlock)
    Shift+X/Y/Z    lock to the plane without that axis
    Ctrl (held)    move freely, no snapping (whatever the mode)
    LMB / Enter    confirm
    RMB / Esc      cancel
"""
import traceback

import bpy
import numpy as np
from bpy_extras import view3d_utils
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty
from mathutils import Vector
from mathutils.geometry import intersect_line_line, intersect_line_plane

from .. import utils
from ..ldraw.importer import snapReader
from . import common,snapmatch

# Snapping \/

def snapStep(mode, gridSize):
    # the grid step for a mode, or None for no grid. BRICK uses the grid when nothing snaps
    return None if mode == 'NONE' else gridSize

# Brick snap data \/

_snapsTried = set()  # meshes already looked up, so parts with no snaps aren't re-parsed every move

def getPrototypes(obj):
    # type index -> (mesh, prototype world matrix) for one lego cloud, for types with snaps
    coll = utils.getModifierInput(obj, "LegoInstancer", "Collection")
    if coll is None:
        return {}
    protos = {}
    for child in coll.objects:  # prototypes are named with their type, e.g. "00005"
        if child.type != 'MESH':
            continue
        t = common.typeFromName(child.name)
        if t is not None:
            protos[t] = (child.data, np.array(child.matrix_world, dtype=np.float64))

    meshes = [m for m, _ in protos.values() if m.as_pointer() not in _snapsTried]
    if meshes:
        try:
            snapReader.buildSnaps(meshes)  # skips meshes that already have snaps
        except Exception:
            print("LEGO EDITOR, WARNING: couldn't build snaps for some prototypes")
            traceback.print_exc()
        _snapsTried.update(m.as_pointer() for m in meshes)

    return {t: p for t, p in protos.items() if len(p[0].lego_snaps) > 0}

def snapInstances(obj, indices, protos, positions, quats, types):
    # [(brick mesh, (k, 4, 4) world matrices)] for the given points whose type has snaps,
    # one entry per brick type
    indices = np.asarray(indices, dtype=np.int64)
    if len(indices) == 0:
        return []
    worlds = common.instanceWorlds(np.array(obj.matrix_world, dtype=np.float64),
                                   positions[indices], quats[indices])
    pointTypes = types[indices]
    out = []
    for t in np.unique(pointTypes):
        proto = protos.get(int(t))
        if proto is not None:
            mesh, protoMatrix = proto
            out.append((mesh, worlds[pointTypes == t] @ protoMatrix))
    return out


class PointSnapper(snapmatch.Snapper):
    """snapmatch.Snapper fed with instanced bricks rather than objects.

    moving, static: [(mesh, (k, 4, 4) world matrices)], numpy.

    A move only translates the bricks, so the moving joints' directions and classes are
    worked out once here, with all moving bricks merged into one set of joints. find() then
    only projects them and searches near the static ones; Snapper.find redid all of that for
    every moving brick on every mouse move.
    """
    def __init__(self, moving, static):
        self._groups = {"": 0}
        self._cache = {}
        self._profIds = {}
        self._profiles = {}
        self._byId = []
        self._fits = {}
        self._compat = {}

        self.tol = snapmatch.RADIUS_TOL * (snapmatch._scale(moving[0][1][0]) if moving else 1.0)

        mv = self._joints(moving)
        st = self._joints(static)
        self.active = mv is not None and st is not None
        if not self.active:
            return

        self.movePos, self.moveAxis, self.moveLdu = mv["pos"], mv["axis"], mv["ldu"]
        self.moveKeys, self.moveMembers = snapmatch._classes(snapmatch._classKeys(
            mv["kind"], mv["male"], mv["group"], mv["prof"], mv["radius"], mv["axis"]))

        self.pos, self.axis = st["pos"], st["axis"]
        self.classKeys, self.members = snapmatch._classes(snapmatch._classKeys(
            st["kind"], st["male"], st["group"], st["prof"], st["radius"], st["axis"]))

    def _joints(self, items):
        # every snap joint of many bricks at once, as concatenated arrays (or None if none).
        # Bricks of one part at one scale share their local joints, so they're done together
        fields = ("pos", "axis", "kind", "male", "group", "radius", "prof", "ldu")
        out = {f: [] for f in fields}
        for mesh, mats in items:
            local = self._local(mesh)
            n = len(local.pos)
            if n == 0 or len(mats) == 0:
                continue
            scales = np.linalg.norm(mats[:, :3, 0], axis=1)  # world size of one LDU, per brick
            rounded = np.round(scales, 6)
            for sc in np.unique(rounded):
                M = mats[rounded == sc]
                k = len(M)
                R, t = M[:, :3, :3], M[:, :3, 3]
                pos = np.einsum('kij,nj->kni', R, local.pos) + t[:, None, :]
                axis = np.einsum('kij,nj->kni', R, local.axis)
                axis /= np.maximum(np.linalg.norm(axis, axis=2, keepdims=True), 1e-12)
                out["pos"].append(pos.reshape(-1, 3))
                out["axis"].append(axis.reshape(-1, 3))
                out["kind"].append(np.tile(local.kind, k))
                out["male"].append(np.tile(local.male, k))
                out["group"].append(np.tile(local.group, k))
                out["radius"].append(np.tile(local.radius * sc, k))
                out["prof"].append(np.tile(self._profileIds(mesh, local, float(sc)), k))
                out["ldu"].append(np.full(k * n, sc))
        if not out["pos"]:
            return None
        return {f: np.concatenate(v) for f, v in out.items()}

    def find(self, context, delta, maxPx=None):
        """World transform that snaps this move, or None if nothing is close enough.

        delta: the free (unsnapped) translation, a mathutils Vector. Returns a Matrix to
        left-multiply onto each moving brick's world matrix, as Snapper.find does.
        """
        if not self.active:
            return None
        if maxPx is None:
            maxPx = snapmatch.SNAP_PX * context.preferences.system.ui_scale
        region = context.region
        lo = np.array([-maxPx, -maxPx])
        hi = np.array([region.width + maxPx, region.height + maxPx])

        def onScreen(px, front):
            return front & np.all((px > lo) & (px < hi), axis=1)

        # every moving joint projected once, at where the free move puts it
        freePx, freeFront = snapmatch._project(context, self.movePos + np.array(delta, dtype=np.float64))
        visible = onScreen(freePx, freeFront)

        targets = {}  # (static class, offset) -> (positions, axes, px), this update
        cand = []     # (pixel distance, depth, target, pivot, moving axis, static axis, LDU size)
        for ka, ia in zip(self.moveKeys, self.moveMembers):
            ia = ia[visible[ia]]
            if len(ia) == 0:
                continue
            for b, off in self._compatible(ka):
                t = targets.get((b, off))
                if t is None:
                    jb = self.members[b]
                    tPos = self.pos[jb] + off * self.axis[jb]
                    tPx, tFront = snapmatch._project(context, tPos)
                    keep = onScreen(tPx, tFront)
                    t = targets[(b, off)] = (tPos[keep], self.axis[jb][keep], tPx[keep])
                tPos, tAxis, tPx = t
                i, j, dist = snapmatch._nearPairs(freePx[ia], tPx, maxPx)
                if len(i) == 0:
                    continue
                snapped = tPos[j]
                m = ia[i]
                cand.append((dist, snapmatch._viewDepth(context, snapped), snapped,
                             self.movePos[m], self.moveAxis[m], tAxis[j], self.moveLdu[m]))

        if not cand:
            return None
        px, depth, target, pivot, mAxis, sAxis, ldu = (np.concatenate(c) for c in zip(*cand))

        # same score as Snapper.find
        tilt = np.arccos(np.clip((mAxis * sAxis).sum(axis=1), -1.0, 1.0))
        extra = (depth - depth.min()) / np.maximum(ldu * snapmatch.DEPTH_RANGE, 1e-12)
        score = (px / maxPx
                 + snapmatch.DEPTH_WEIGHT * np.minimum(extra, 1.0)
                 + snapmatch.ALIGN_WEIGHT * tilt / snapmatch.ALIGN_ANGLE)
        k = int(np.argmin(score))
        return snapmatch._snapMatrix(pivot[k], mAxis[k], target[k], sAxis[k])


def buildSnapper(context, clouds):
    # The moving bricks are the selected points; everything else visible with snaps is static:
    # unselected points, other lego clouds and ordinary lego brick objects
    moving, static = [], []
    movingObjs = {c.obj for c in clouds}

    for cloud in clouds:
        protos = getPrototypes(cloud.obj)
        types = common.readTypes(cloud.pc)
        args = (protos, cloud.orig, cloud.origRot, types)
        moving += snapInstances(cloud.obj, np.flatnonzero(cloud.mask), *args)
        static += snapInstances(cloud.obj, np.flatnonzero(~cloud.mask), *args)

    for obj in context.visible_objects:
        if obj in movingObjs:
            continue
        if common.isLegoPointCloud(obj) and len(obj.data.points) > 0:
            pc = obj.data
            static += snapInstances(obj, np.arange(len(pc.points)), getPrototypes(obj),
                                    common.readPositions(pc), common.readRotations(pc),
                                    common.readTypes(pc))
        elif snapmatch.hasSnaps(obj):
            static.append((obj.data, np.array(obj.matrix_world, dtype=np.float64)[None]))

    return PointSnapper(moving, static)

# Operator itself \/

class LEGO_OT_MoveInstance(bpy.types.Operator):
    """Move the selected bricks, snapping to other bricks or the LDU grid. Hold Ctrl to move freely"""
    bl_idname = "lego.move_instance"
    bl_label = "Move Bricks"
    bl_options = {'REGISTER', 'UNDO'}

    grid: FloatProperty(name="Grid Size", description="Snap step in LDU",
                        default=1.0, min=0.001, soft_max=40.0, options={'SKIP_SAVE'})
    absolute: BoolProperty(name="Snap Absolute",
                           description="Snap the first selected brick onto the grid, instead of snapping the distance moved",
                           default=False)
    offset: FloatVectorProperty(name="Offset", description="World space move, before snapping",
                                subtype='TRANSLATION', size=3)
    # how the move was snapped, so the redo panel repeats it: on the grid, or onto a brick
    # (a brick snap depends on the view, so the snap itself is stored)
    use_grid: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})
    snapped: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})
    snap_matrix: FloatVectorProperty(size=16, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return common.pollLegoEdit(context)  # otherwise G falls through to Blender's move

    # mouse maths \/

    def mouseRay(self, context, event):
        coord = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
        direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
        return origin, direction

    def mouseOnViewPlane(self, context, event):
        coord = (event.mouse_region_x, event.mouse_region_y)
        return view3d_utils.region_2d_to_location_3d(context.region, context.region_data, coord, self.pivot)

    def worldDelta(self, context, event):
        origin, direction = self.mouseRay(context, event)
        far = origin + direction

        if self.axes is None:
            hit = self.mouseOnViewPlane(context, event)
        elif len(self.axes) == 1:
            # closest point on the axis line to the mouse ray
            axis = Vector(((1, 0, 0), (0, 1, 0), (0, 0, 1))[self.axes[0]])
            result = intersect_line_line(origin, far, self.pivot, self.pivot + axis)
            hit = result[1] if result else None
        else:
            # mouse ray onto the plane through the pivot, normal = the excluded axis
            normal = Vector(((1, 0, 0), (0, 1, 0), (0, 0, 1))[({0, 1, 2} - set(self.axes)).pop()])
            hit = intersect_line_plane(origin, far, self.pivot, normal)

        if hit is None:  # ray parallel to the axis/plane: keep the last move
            return self.lastDelta

        delta = hit - self.grabStart
        if self.axes is not None:
            delta = Vector([delta[a] if a in self.axes else 0.0 for a in range(3)])
        return delta

    # snapping \/

    def getSnapper(self, context):
        # built on first use, so GRID / NONE moves never pay for it
        if self.snapper is None:
            self.snapper = buildSnapper(context, self.clouds)
        return self.snapper

    def applyMove(self, context, clouds, delta, mode, snap=None):
        # snap: a brick snap matrix to use, or None to use the grid (or nothing for NONE).
        # Returns the first cloud's LDU move for the header, or None when brick snapped
        if snap is not None:
            for cloud in clouds:
                cloud.applyTransform(snap)
            return None
        step = snapStep(mode, self.grid)
        lduDeltas = [c.lduDelta(delta, step, self.absolute) for c in clouds]
        for cloud, lduDelta in zip(clouds, lduDeltas):
            cloud.apply(lduDelta)
        return lduDeltas[0]

    # modal \/

    def invoke(self, context, event):
        self.clouds = common.gatherClouds(context)
        if not self.clouds:
            return {'PASS_THROUGH'}  # nothing selected: let Blender's G deal with it

        # read the scene settings; grid size can still be set by the keymap item / caller
        scene = context.scene
        self.baseMode = scene.lego_snap_mode  # Ctrl drops to NONE, releasing it comes back here
        if not self.properties.is_property_set("grid"):
            self.grid = scene.lego_grid_size
        self.snapper = None

        self.pivot = common.selectionCentre(self.clouds)
        self.grabStart = self.mouseOnViewPlane(context, event)
        self.axes = None
        self.lastDelta = Vector()

        context.window_manager.modal_handler_add(self)
        self.update(context, event)
        return {'RUNNING_MODAL'}

    def update(self, context, event):
        delta = self.worldDelta(context, event)
        self.lastDelta = delta
        self.offset = delta
        mode = 'NONE' if event.ctrl else self.baseMode

        snap = None
        if mode == 'BRICK':
            snapper = self.getSnapper(context)
            if snapper.active:
                snap = snapper.find(context, delta)
        self.snapped = snap is not None
        self.use_grid = snap is None and mode != 'NONE'
        if snap is not None:
            self.snap_matrix = [v for row in snap for v in row]

        d = self.applyMove(context, self.clouds, delta, mode, snap)

        axisText = "" if self.axes is None else "  along " + "".join("XYZ"[a] for a in self.axes)
        if d is None:
            moveText = "snapped to brick"
        else:
            snapText = "free" if mode == 'NONE' else f"grid {self.grid:g} LDU"
            moveText = f"D: {d[0]:.4g}  {d[1]:.4g}  {d[2]:.4g} LDU  ({snapText})"
        context.area.header_text_set(
            f"Move  {moveText}{axisText}"
            "    X/Y/Z: axis   Shift+X/Y/Z: plane   Ctrl: free   LMB: confirm   RMB: cancel")
        context.area.tag_redraw()

    def setAxis(self, event):
        axis = 'XYZ'.index(event.type)
        newAxes = tuple(a for a in (0, 1, 2) if a != axis) if event.shift else (axis,)
        self.axes = None if self.axes == newAxes else newAxes  # pressing again unlocks

    def modal(self, context, event):
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}:
            return {'PASS_THROUGH'}  # allow zooming while moving

        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            self.update(context, event)

        elif event.type in {'X', 'Y', 'Z'} and event.value == 'PRESS':
            self.setAxis(event)
            self.update(context, event)

        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'} and event.value == 'PRESS':
            common.clearHeader(context)
            return {'FINISHED'}

        elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            for cloud in self.clouds:
                cloud.restore()
            common.clearHeader(context)
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def execute(self, context):
        # used by the redo panel (F9 / bottom left): repeats the move from the stored values.
        clouds = common.gatherClouds(context)
        if not clouds:
            return {'CANCELLED'}
        snap = None
        if self.snapped:
            snap = np.array(self.snap_matrix, dtype=np.float64).reshape(4, 4)
        mode = 'GRID' if self.use_grid else 'NONE'
        self.applyMove(context, clouds, Vector(self.offset), mode, snap)
        return {'FINISHED'}

    def draw(self, context):
        # redo panel: only show what does something for the current mode
        layout = self.layout
        if self.snapped:
            layout.label(text="Snapped to brick")
            return
        if self.use_grid:
            layout.prop(self, "grid")
            layout.prop(self, "absolute")
        layout.prop(self, "offset")

# Header UI \/

def drawSnapHeader(self, context):
    # the snapping mode and grid size in the 3D view header, next to Blender's own snapping
    if not LEGO_OT_MoveInstance.poll(context):
        return
    scene = context.scene
    row = self.layout.row(align=True)
    row.prop(scene, "lego_snap_mode", text="")
    sub = row.row(align=True)
    sub.active = scene.lego_snap_mode != 'NONE'  # greyed out, but still editable, when unused
    sub.prop(scene, "lego_grid_size", text="Grid")

# Registration \/

keymaps = common.Keymaps()

def register():
    bpy.utils.register_class(LEGO_OT_MoveInstance)
    bpy.types.VIEW3D_HT_header.append(drawSnapHeader)
    keymaps.add(LEGO_OT_MoveInstance.bl_idname, 'G', 'PRESS')

def unregister():
    keymaps.clear()
    bpy.types.VIEW3D_HT_header.remove(drawSnapHeader)
    bpy.utils.unregister_class(LEGO_OT_MoveInstance)
