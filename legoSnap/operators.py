import bpy
from bpy_extras import view3d_utils

import time
from mathutils import Matrix, Quaternion, Vector

from . import brickBuilder, datParser

class LE_OT_ImportBrick(bpy.types.Operator):
    bl_idname = "lego.import_brick"
    bl_label = "Import Brick"
    bl_options = {'REGISTER'}

    def execute(self,context):
        start = time.perf_counter()
        wm = bpy.context.window_manager
        brickId = wm.brick_id
        pieceLibrary = bpy.context.preferences.addons[__package__].preferences.pieceLibrary
        shadowLibrary = bpy.context.preferences.addons[__package__].preferences.shadowLibrary
        if not brickId:
            self.report({'WARNING'},"Please provide a brick ID")
            return {'CANCELLED'}

        datParser.libraryDir = pieceLibrary
        datParser.shadowDir = shadowLibrary
        obj = brickBuilder.makeBrick(brickId)
        if obj:
            duration = round(time.perf_counter() - start,4)*1000
            self.report({'INFO'},f"Brick built in {duration}ms")
            context.scene.collection.objects.link(obj)
            return {'FINISHED'}
        else:
            self.report({'WARNING'},"No brick found for given ID")
            return {'CANCELLED'}

def objsInRadius(center_obj, radius, include_types=None, exclude_center=True):
    """Return a list of objects whose world-space origin is within `radius` of center_obj."""
    center = center_obj.matrix_world.translation
    radius_sq = radius * radius  # compare squared distances, avoids sqrt
    found = []

    for obj in bpy.context.scene.objects:
        if exclude_center and obj == center_obj:
            continue
        if include_types and obj.type not in include_types:
            continue

        offset = obj.matrix_world.translation - center
        if offset.length_squared <= radius_sq:
            found.append(obj)

    return found

def worldSnaps(obj):
    # (index, snapData, worldPos, worldAxis) for every snap on obj
    mw = obj.matrix_world
    out = []
    for i, s in enumerate(obj.data.lego_snaps):
        m = mw @ snapMatrix(s)
        # LDraw's local +Y (the snap axis) becomes -Z after your LDtoBL conjugation
        axis = (m.to_3x3() @ Vector((0, 0, -1))).normalized()
        out.append((i, s, m.translation.copy(), axis))
    return out

def snapsCompatible(a, b):
    if a.kind != b.kind:
        return False
    return {a.gender.upper(), b.gender.upper()} == {"M", "F"}

def findSnapCandidates(movingObj, others, maxDist=0.2, minAlign=0.99):
    # Returns [(distance, mySnapIndex, otherObjName, otherSnapIndex)], nearest first
    mine = worldSnaps(movingObj)
    if not mine:
        return []
    candidates = []
    for other in others:
        if other is movingObj or not len(other.data.lego_snaps):
            continue
        for j, oSnap, oPos, oAxis in worldSnaps(other):
            for i, mSnap, mPos, mAxis in mine:
                if not snapsCompatible(mSnap, oSnap):
                    continue
                dist = (mPos - oPos).length
                if dist > maxDist:
                    continue
                if abs(mAxis.dot(oAxis)) < minAlign:  # axes must be parallel
                    continue
                candidates.append((dist, i, other.name, j))
    candidates.sort(key=lambda c: c[0])
    return candidates

def snapTransform(movingObj, myIndex, other, otherIndex):
    # Returns a new matrix_world for movingObj that seats its snap on other's snap
    mw = movingObj.matrix_world
    mySnap = mw @ snapMatrix(movingObj.data.lego_snaps[myIndex])
    theirSnap = other.matrix_world @ snapMatrix(other.data.lego_snaps[otherIndex])

    snapAxis = Vector((0, 0, -1))  # LDraw +Y after your LDtoBL conversion
    myAxis = (mySnap.to_3x3() @ snapAxis).normalized()
    theirAxis = (theirSnap.to_3x3() @ snapAxis).normalized()

    # 1. rotate the brick about my snap point so the axes line up
    myPos = mySnap.translation
    rot = myAxis.rotation_difference(theirAxis).to_matrix().to_4x4()
    pivot = Matrix.Translation(myPos) @ rot @ Matrix.Translation(-myPos)

    # 2. slide my snap point onto theirs
    move = Matrix.Translation(theirSnap.translation - myPos)

    return move @ pivot @ mw

class LE_OT_SnapBuild(bpy.types.Operator):
    bl_idname = "lego.snap_build"
    bl_label = "Snap Build"
    bl_options = {'REGISTER'}

    def mouseCoord(self, event):
        return (event.mouse_x - self.region.x, event.mouse_y - self.region.y)

    def mouseTo3d(self, event):
        # point under the mouse on the view-facing plane through the start position
        return view3d_utils.region_2d_to_location_3d(
            self.region, self.rv3d, self.mouseCoord(event), self.startLoc)

    def invoke(self, context, event):
        self.obj = context.active_object
        if not self.obj or context.area.type != 'VIEW_3D':
            self.report({'WARNING'}, "Needs an active object in the 3D viewport")
            return {'CANCELLED'}

        self.area = context.area
        self.region = next(r for r in self.area.regions if r.type == 'WINDOW')
        self.rv3d = self.area.spaces.active.region_3d

        self.startMatrix = self.obj.matrix_world.copy()
        self.startLoc = self.startMatrix.translation.copy()
        # keep the brick where it is relative to the cursor, like G does
        self.grabOffset = self.startLoc - self.mouseTo3d(event)
        self.snapCandidates = []

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}

        if event.type == 'MOUSEMOVE':
            # free position: start orientation, translation follows the mouse
            m = self.startMatrix.copy()
            m.translation = self.mouseTo3d(event) + self.grabOffset
            self.obj.matrix_world = m
            context.view_layer.update()

            nearby = objsInRadius(self.obj, 5.0, include_types={'MESH'})
            self.snapCandidates = findSnapCandidates(self.obj, nearby)
            if self.snapCandidates:
                _, i, otherName, j = self.snapCandidates[0]
                other = bpy.data.objects.get(otherName)
                if other:
                    self.obj.matrix_world = snapTransform(self.obj, i, other, j)

        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            return {'FINISHED'}

        elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self.obj.matrix_world = self.startMatrix  # restore, like cancelling G
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

def snapMatrix(s):
    return Matrix.Translation(Vector(s.location)) @ Quaternion(s.rotation).to_matrix().to_4x4()

def getSnapBricks(context):
    # selected objects that carry snap data
    return [o for o in context.selected_objects
            if o.type == 'MESH' and len(o.data.lego_snaps)]

class LE_GGT_SnapPoints(bpy.types.GizmoGroup):
    bl_idname = "LE_GGT_snap_points"
    bl_label = "Lego Snap Points"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT', 'SHOW_MODAL_ALL'}

    maleCol = (1.0, 0.35, 0.2)
    femaleCol = (0.2, 0.6, 1.0)

    @classmethod
    def poll(cls, context):
        return context.window_manager.lego_show_snaps and bool(getSnapBricks(context))

    def setup(self, context):
        self.layoutKey = None
        self.sync(context)

    def refresh(self, context):
        # called when selection / data changes: rebuild only if the layout differs
        self.sync(context)

    def draw_prepare(self, context):
        # called every redraw: keep gizmos glued to their bricks as they move
        # entries store names/indices, not live references, which go stale when an object is deleted
        for gz, (objName, i) in zip(self.gizmos, self.entries):
            obj = bpy.data.objects.get(objName)
            if obj is None:
                # object was deleted; refresh() will rebuild the gizmos
                gz.hide = True
                continue
            gz.hide = False
            gz.matrix_basis = obj.matrix_world @ snapMatrix(obj.data.lego_snaps[i])

    def sync(self, context):
        bricks = getSnapBricks(context)
        key = tuple((o.name, len(o.data.lego_snaps)) for o in bricks)
        if key == self.layoutKey:
            return
        self.layoutKey = key

        for gz in list(self.gizmos):
            self.gizmos.remove(gz)

        self.entries = []
        for obj in bricks:
            for i, s in enumerate(obj.data.lego_snaps):
                gz = self.gizmos.new("GIZMO_GT_move_3d")
                gz.draw_style = 'RING_2D'
                gz.draw_options = {'FILL','ALIGN_VIEW'}
                gz.hide_select = True
                gz.scale_basis = 0.08
                isMale = s.gender.upper() == 'M'
                gz.color = self.maleCol if isMale else self.femaleCol
                gz.alpha = 0.8
                self.entries.append((obj.name, i))

class LE_OT_ToggleSnaps(bpy.types.Operator):
    bl_idname = "lego.toggle_snaps"
    bl_label = "Toggle Snap Points"
    bl_description = "Toggle drawing of snap points on selected bricks"

    def execute(self, context):
        wm = context.window_manager
        wm.lego_show_snaps = not wm.lego_show_snaps
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return {'FINISHED'}


classes = (LE_OT_SnapBuild, LE_OT_ImportBrick, LE_OT_ToggleSnaps, LE_GGT_SnapPoints)

addonKeymaps = []

def register():
    bpy.types.WindowManager.lego_show_snaps = bpy.props.BoolProperty(default=False)
    for c in classes:
        bpy.utils.register_class(c)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:  # None when Blender runs in background mode
        km = kc.keymaps.new(name='Object Mode', space_type='EMPTY')
        kmi = km.keymap_items.new(LE_OT_SnapBuild.bl_idname, 'G', 'PRESS', shift=True)
        addonKeymaps.append((km, kmi))

def unregister():
    for km, kmi in addonKeymaps:
        km.keymap_items.remove(kmi)
    addonKeymaps.clear()

    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.WindowManager.lego_show_snaps
