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
from . import common, snapmatch

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
    # (point index, brick mesh, brick world matrix) for the given points whose type has snaps
    indices = [int(i) for i in indices if int(types[i]) in protos]
    if not indices:
        return []
    worlds = common.instanceWorlds(np.array(obj.matrix_world, dtype=np.float64),
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
        types = common.readTypes(cloud.pc)
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
                common.readPositions(pc), common.readRotations(pc), common.readTypes(pc))]
        elif snapmatch.hasSnaps(obj):
            static.append((obj.data, np.array(obj.matrix_world, dtype=np.float64)))

    return PointSnapper(moving, static), base

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
