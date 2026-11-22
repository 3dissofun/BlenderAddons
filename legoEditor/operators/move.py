"""
The intent of this file is to provide an override to the move operator (G) when in a lego point cloud.

Point positions are stored in LDU in the cloud's local space, and the object's matrix (with its
scale) places them:
    brick world matrix = object matrix @ T(position) @ R(rotation) @ prototype matrix

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
from ..tools import snapmatch
from . import common

AXIS_KEYS = {'X': 0, 'Y': 1, 'Z': 2}
AXIS_VECTORS = (Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
NAV_EVENTS = {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}

# Point cloud attributes \/

def readPositions(pc):
    n = len(pc.points)
    flat = np.empty(n * 3, dtype=np.float32)
    pc.attributes["position"].data.foreach_get("vector", flat)
    return flat.reshape(n, 3)

def writePositions(pc, positions):
    pc.attributes["position"].data.foreach_set("vector", positions.astype(np.float32).ravel())
    pc.update_tag()

def readRotations(pc):
    # (n, 4) w x y z; points without a rotation attribute are unrotated
    n = len(pc.points)
    attr = pc.attributes.get("rotation")
    if attr is None:
        out = np.zeros((n, 4), dtype=np.float32)
        out[:, 0] = 1.0
        return out
    flat = np.empty(n * 4, dtype=np.float32)
    attr.data.foreach_get("value", flat)
    return flat.reshape(n, 4)

def writeRotations(pc, quats):
    attr = pc.attributes.get("rotation")
    if attr is None:
        attr = pc.attributes.new("rotation", 'QUATERNION', 'POINT')
    attr.data.foreach_set("value", quats.astype(np.float32).ravel())
    pc.update_tag()

def readTypes(pc):
    n = len(pc.points)
    attr = pc.attributes.get("type")
    vals = np.zeros(n, dtype=np.int32)
    if attr is not None:
        attr.data.foreach_get("value", vals)
    return vals

def readSelection(pc):
    # Blender treats a missing .selection attribute as "everything selected"
    n = len(pc.points)
    attr = pc.attributes.get(".selection")
    if attr is None:
        return np.ones(n, dtype=bool)
    if attr.data_type == 'BOOLEAN':
        vals = np.zeros(n, dtype=bool)
        attr.data.foreach_get("value", vals)
        return vals
    vals = np.zeros(n, dtype=np.float32)
    attr.data.foreach_get("value", vals)
    return vals > 0.5

# Maths \/

def quatsToMatrices(quats):
    # (n, 4) w x y z -> (n, 3, 3)
    q = np.asarray(quats, dtype=np.float64)
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    w, x, y, z = q.T
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
        np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
        np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1),
    ], 1)

def instanceWorlds(objMatrix, positions, quats):
    # world matrix of each instanced brick, before its prototype's own matrix (see top of file)
    n = len(positions)
    local = np.zeros((n, 4, 4))
    local[:, :3, :3] = quatsToMatrices(quats)
    local[:, :3, 3] = np.asarray(positions, dtype=np.float64)
    local[:, 3, 3] = 1.0
    return objMatrix @ local

def snapStep(mode, gridSize):
    # the grid step for a mode, or None for no grid. BRICK uses the grid when nothing snaps
    return None if mode == 'NONE' else gridSize

def snapToGrid(values, step):
    return np.round(values / step) * step

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
        try:
            protos[int(child.name[:5])] = (child.data, np.array(child.matrix_world, dtype=np.float64))
        except ValueError:
            continue

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
    # (point index, brick mesh, brick world matrix) for the given points whose type has snaps
    indices = [int(i) for i in indices if int(types[i]) in protos]
    if not indices:
        return []
    worlds = instanceWorlds(np.array(obj.matrix_world, dtype=np.float64),
                            positions[indices], quats[indices])
    out = []
    for i, world in zip(indices, worlds):
        mesh, protoMatrix = protos[int(types[i])]
        out.append((i, mesh, world @ protoMatrix))
    return out


class PointSnapper(snapmatch.Snapper):
    """snapmatch.Snapper fed with instanced bricks rather than objects.

    moving: [(key, mesh, world matrix)], static: [(mesh, world matrix)], numpy 4x4 matrices.
    find(context, base, delta) works as before, with base = {key: world matrix}.
    """
    def __init__(self, moving, static):
        self._groups = {"": 0}
        self._cache = {}
        self._profIds = {}
        self._profiles = {}
        self._byId = []
        self._fits = {}
        self._compat = {}

        self.tol = snapmatch.RADIUS_TOL * (snapmatch._scale(moving[0][2]) if moving else 1.0)

        self.moving = []
        for key, mesh, matrix in moving:
            local = self._local(mesh)
            self.moving.append((key, local, self._profileIds(mesh, local, snapmatch._scale(matrix))))

        pos, axis, kind, male, group, radius, prof = [], [], [], [], [], [], []
        for mesh, matrix in static:
            local = self._local(mesh)
            sc = snapmatch._scale(matrix)
            p, a = local.world(matrix)
            pos.append(p)
            axis.append(a)
            kind.append(local.kind)
            male.append(local.male)
            group.append(local.group)
            radius.append(local.radius * sc)
            prof.append(self._profileIds(mesh, local, sc))

        self.active = bool(self.moving and pos and any(len(l.pos) for _, l, _ in self.moving))
        if not self.active:
            return
        self.pos = np.concatenate(pos)
        self.axis = np.concatenate(axis)
        self.active = len(self.pos) > 0
        keys = snapmatch._classKeys(np.concatenate(kind), np.concatenate(male), np.concatenate(group),
                                    np.concatenate(prof), np.concatenate(radius), self.axis)
        self.classKeys, self.members = snapmatch._classes(keys)


def buildSnapper(context, clouds):
    # The moving bricks are the selected points; everything else visible with snaps is static:
    # unselected points, other lego clouds and ordinary lego brick objects
    moving, base, static = [], {}, []
    movingObjs = {c.obj for c in clouds}

    for ci, cloud in enumerate(clouds):
        protos = getPrototypes(cloud.obj)
        types = readTypes(cloud.pc)
        args = (protos, cloud.orig, cloud.origRot, types)
        for i, mesh, world in snapInstances(cloud.obj, np.flatnonzero(cloud.mask), *args):
            moving.append(((ci, i), mesh, world))
            base[(ci, i)] = world
        static += [(m, w) for _, m, w in snapInstances(cloud.obj, np.flatnonzero(~cloud.mask), *args)]

    for obj in context.visible_objects:
        if obj in movingObjs:
            continue
        if common.isLegoPointCloud(obj) and len(obj.data.points) > 0:
            pc = obj.data
            static += [(m, w) for _, m, w in snapInstances(
                obj, range(len(pc.points)), getPrototypes(obj),
                readPositions(pc), readRotations(pc), readTypes(pc))]
        elif snapmatch.hasSnaps(obj):
            static.append((obj.data, np.array(obj.matrix_world, dtype=np.float64)))

    return PointSnapper(moving, static), base

# Moving clouds \/

class CloudMove:
    # The state of one point cloud being moved: its original positions and rotations, which
    # points move, and how to turn a world space move into an LDU move in its local space
    def __init__(self, obj):
        self.obj = obj
        self.pc = obj.data
        self.orig = readPositions(self.pc)
        self.origRot = readRotations(self.pc)
        self.mask = readSelection(self.pc)
        self.matrix = np.array(obj.matrix_world, dtype=np.float64)
        # a direction, so no translation: undo the object's rotation and scale
        self.worldToLdu = obj.matrix_world.inverted_safe().to_3x3()
        self.anchor = self.orig[self.mask][0].astype(np.float64) if self.mask.any() else None
        self.tilted = False  # rotations currently differ from origRot

    def worldPoints(self):
        # world space positions of the selected points, as drawn by the instancer
        mat = self.obj.matrix_world
        return [mat @ Vector(p) for p in self.orig[self.mask]]

    def lduDelta(self, worldDelta, step=None, absolute=False):
        delta = np.array(self.worldToLdu @ worldDelta, dtype=np.float64)
        if step is None:
            return delta
        if absolute:
            # put the first selected brick on the grid, the rest keep their offsets to it
            return snapToGrid(self.anchor + delta, step) - self.anchor
        # snap the distance moved, so bricks already off grid stay as they were relative to it
        return snapToGrid(delta, step)

    def apply(self, lduDelta):
        positions = self.orig.copy()
        positions[self.mask] += lduDelta.astype(np.float32)
        writePositions(self.pc, positions)
        if self.tilted:  # coming off a brick snap that tilted the bricks
            writeRotations(self.pc, self.origRot)
            self.tilted = False

    def applySnap(self, snap):
        # snap: world 4x4 to left-multiply onto each moving brick's world matrix.
        # In the cloud's local space that's A = M^-1 @ snap @ M, applied to T(p) @ R
        A = np.linalg.inv(self.matrix) @ np.asarray(snap, dtype=np.float64) @ self.matrix
        rot, move = A[:3, :3], A[:3, 3]
        sel = self.mask

        positions = self.orig.astype(np.float64)
        positions[sel] = positions[sel] @ rot.T + move
        writePositions(self.pc, positions)

        quats = self.origRot.copy()
        quats[sel] = utils.matricesToQuaternions(rot @ quatsToMatrices(self.origRot[sel]))
        writeRotations(self.pc, quats)
        self.tilted = True

    def restore(self):
        writePositions(self.pc, self.orig)
        writeRotations(self.pc, self.origRot)


def gatherClouds(context):
    clouds = [CloudMove(o) for o in context.objects_in_mode
              if common.isLegoPointCloud(o) and len(o.data.points) > 0]
    return [c for c in clouds if c.mask.any()]

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
        # same rules as select: only in point cloud edit mode, in the 3D viewport,
        # with a lego cloud being edited. Otherwise G falls through to Blender's move.
        if context.mode != 'EDIT_POINTCLOUD':
            return False
        if context.area is None or context.area.type != 'VIEW_3D':
            return False
        return any(common.isLegoPointCloud(o) for o in context.objects_in_mode)

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
            axis = AXIS_VECTORS[self.axes[0]]
            result = intersect_line_line(origin, far, self.pivot, self.pivot + axis)
            hit = result[1] if result else None
        else:
            # mouse ray onto the plane through the pivot, normal = the excluded axis
            normal = AXIS_VECTORS[({0, 1, 2} - set(self.axes)).pop()]
            hit = intersect_line_plane(origin, far, self.pivot, normal)

        if hit is None:  # ray parallel to the axis/plane: keep the last move
            return self.lastDelta

        delta = hit - self.grabStart
        if self.axes is not None:
            delta = sum((AXIS_VECTORS[a] * delta[a] for a in self.axes), Vector())
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
                cloud.applySnap(snap)
            return None
        step = snapStep(mode, self.grid)
        lduDeltas = [c.lduDelta(delta, step, self.absolute) for c in clouds]
        for cloud, lduDelta in zip(clouds, lduDeltas):
            cloud.apply(lduDelta)
        return lduDeltas[0]

    # modal \/

    def invoke(self, context, event):
        self.clouds = gatherClouds(context)
        if not self.clouds:
            return {'PASS_THROUGH'}  # nothing selected: let Blender's G deal with it

        # read the scene settings; grid size can still be set by the keymap item / caller
        scene = context.scene
        self.baseMode = scene.lego_snap_mode  # Ctrl drops to NONE, releasing it comes back here
        if not self.properties.is_property_set("grid"):
            self.grid = scene.lego_grid_size
        self.snapper = None

        points = [p for c in self.clouds for p in c.worldPoints()]
        self.pivot = sum(points, Vector()) / len(points)
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
            snapper, base = self.getSnapper(context)
            if snapper.active:
                snap = snapper.find(context, base, delta)
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
        axis = AXIS_KEYS[event.type]
        newAxes = tuple(a for a in (0, 1, 2) if a != axis) if event.shift else (axis,)
        self.axes = None if self.axes == newAxes else newAxes  # pressing again unlocks

    def finish(self, context):
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        if event.type in NAV_EVENTS:
            return {'PASS_THROUGH'}  # allow zooming while moving

        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            self.update(context, event)

        elif event.type in AXIS_KEYS and event.value == 'PRESS':
            self.setAxis(event)
            self.update(context, event)

        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'} and event.value == 'PRESS':
            self.finish(context)
            return {'FINISHED'}

        elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            for cloud in self.clouds:
                cloud.restore()
            self.finish(context)
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def execute(self, context):
        # used by the redo panel (F9 / bottom left): repeats the move from the stored values.
        clouds = gatherClouds(context)
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

# Keymap \/

addon_keymaps = []

def registerKeymap():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:  # background mode
        return

    # point cloud edit mode keymap; mode keymaps run before the generic "3D View" one
    km = kc.keymaps.new(name="Point Cloud", space_type='EMPTY')
    kmi = km.keymap_items.new(LEGO_OT_MoveInstance.bl_idname, 'G', 'PRESS')
    addon_keymaps.append((km, kmi))

def unregisterKeymap():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

def register():
    bpy.utils.register_class(LEGO_OT_MoveInstance)
    bpy.types.VIEW3D_HT_header.append(drawSnapHeader)
    registerKeymap()

def unregister():
    unregisterKeymap()
    bpy.types.VIEW3D_HT_header.remove(drawSnapHeader)
    bpy.utils.unregister_class(LEGO_OT_MoveInstance)
