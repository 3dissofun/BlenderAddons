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

class LE_OT_SnapBuild(bpy.types.Operator):
    bl_idname = "lego.snap_build"
    bl_label = "Snap Build"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):

        # Active object and start location
        self.obj = context.active_object
        self.area = context.area
        self.startLoc = self.obj.location.copy()

        self.report({'INFO'},"Build mode active!")
        print("Build mode active!")
        
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            self.obj = context.active_object
            if not self.obj:
                return {'RUNNING_MODAL'}

            # Move the active object to the intersection of it and other geometry
            region = next(r for r in self.area.regions if r.type == 'WINDOW')
            coord = (event.mouse_x - region.x, event.mouse_y - region.y)
            rv3d = self.area.spaces.active.region_3d
            origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
            direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
 
            # ray_cast can't ignore objects, so hide the moving one while casting
            self.obj.hide_set(True)
            context.view_layer.update()
            hit, loc, normal, index, hitObj, mat = context.scene.ray_cast(
                context.evaluated_depsgraph_get(), origin, direction)
            self.obj.hide_set(False)
 
            if hit:
                self.obj.location = loc
            
        elif event.type == "LEFTMOUSE" and event.value == "PRESS":
            self.report({'INFO'},"Mouse Click")
            print("Mouse click")
            return {"RUNNING_MODAL"}
        
        elif event.type in {"RET","ESC","NUMPAD_ENTER"}:
            self.report({'INFO'},"Build mode quit")
            print("Build mode quit")

            return {"FINISHED"}
 
        return {"RUNNING_MODAL"}


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

def register():
    bpy.types.WindowManager.lego_show_snaps = bpy.props.BoolProperty(default=False)
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.WindowManager.lego_show_snaps
