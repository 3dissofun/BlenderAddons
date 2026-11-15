"""
Brick asset shelf for the Lego Editor (Blender 4.2+).

The shelf shows the placeholder assets made by buildBrickShelfLibrary.py: empty
objects that only carry a preview, name, tags and catalog. The LDraw filename
is in asset_data.description. The placeholders are never loaded; clicking or
dragging one builds the real brick with getBrickMesh().

    Click   -> add the brick at the 3D cursor
    Drag    -> brick follows the mouse over the scene, release to place
               while dragging: R / wheel = rotate 90°, Ctrl = no grid,
               Esc / right-click = cancel

Search: the shelf's own search box filters on asset names AND tags, so it
matches part names ("Brick 2 x 4"), part numbers ("3001") and size tags ("2x4").

Lives in the addon root (legoEditor/assetShelf.py); register it from __init__.py.
"""

import math

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector, Matrix, geometry

from .ldraw.importer.importer import getBrickMesh, getBrickObj

GRID = 20.0          # stud pitch in LDU (1 Blender unit = 1 LDU)
PLATE = 8.0          # plate height in LDU


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def asset_file(context):
    """LDraw filename of the asset in context, or None."""
    asset = getattr(context, "asset", None)
    if asset is None or asset.metadata is None:
        return None
    file = asset.metadata.description
    return file if file.endswith(".dat") else None


def new_brick(context, file):
    """Get the brick's mesh (local file -> brick library -> built from source)
    and wrap it in a new object linked to the active collection."""
    mesh = getBrickMesh(file)
    if mesh is None:
        return None
    obj = getBrickObj(mesh)          # bpy.data.objects.new + is_lego
    context.collection.objects.link(obj)
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


def bottom_offset(obj):
    """Distance from the object's origin down to its lowest point (local Z)."""
    return -min(Vector(c).z for c in obj.bound_box)


def view3d_window(context):
    """(region, region_3d) of the 3D view's main region, whatever region the
    operator started in (the shelf is its own region)."""
    area = context.area
    if area is None or area.type != 'VIEW_3D':
        return None, None
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    return region, area.spaces.active.region_3d


# ---------------------------------------------------------------------------
# Shelf
# ---------------------------------------------------------------------------

class LEGO_AST_brick_shelf(bpy.types.AssetShelf):
    bl_space_type = 'VIEW_3D'
    bl_idname = "VIEW3D_AST_lego_bricks"
    bl_options = {'DEFAULT_VISIBLE'}
    bl_activate_operator = "lego.add_brick"   # click
    bl_drag_operator = "lego.drag_brick"      # drag
    bl_default_preview_size = 96

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    @classmethod
    def asset_poll(cls, asset):
        # Only the brick placeholders: their description holds the .dat filename
        meta = asset.metadata
        return (asset.id_type == 'OBJECT' and meta is not None
                and meta.description.endswith(".dat"))

    @classmethod
    def draw_context_menu(cls, context, asset, layout):
        layout.operator("lego.add_brick", text="Add at 3D Cursor")


# ---------------------------------------------------------------------------
# Asset reference properties
# ---------------------------------------------------------------------------

def _library_type_items():
    # Same items as bpy.types.AssetWeakReference.asset_library_type
    prop = bpy.types.AssetWeakReference.bl_rna.properties["asset_library_type"]
    return [(i.identifier, i.name, i.description) for i in prop.enum_items]


class AssetRefProps:
    """The shelf writes the clicked/dragged asset into these three properties
    (matching bpy.types.AssetWeakReference) before running the operator.
    We read the asset from context.asset instead, but they must exist."""
    asset_library_type: bpy.props.EnumProperty(
        items=_library_type_items(), options={'HIDDEN', 'SKIP_SAVE'})
    asset_library_identifier: bpy.props.StringProperty(
        options={'HIDDEN', 'SKIP_SAVE'})
    relative_asset_identifier: bpy.props.StringProperty(
        options={'HIDDEN', 'SKIP_SAVE'})


# ---------------------------------------------------------------------------
# Click: add at cursor
# ---------------------------------------------------------------------------

class LEGO_OT_add_brick(AssetRefProps, bpy.types.Operator):
    """Add this brick at the 3D cursor"""
    bl_idname = "lego.add_brick"
    bl_label = "Add Brick"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return asset_file(context) is not None

    def execute(self, context):
        file = asset_file(context)
        obj = new_brick(context, file)
        if obj is None:
            self.report({'WARNING'}, f"Couldn't build {file}")
            return {'CANCELLED'}
        obj.location = context.scene.cursor.location
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Drag: brick follows the mouse until release
# ---------------------------------------------------------------------------

class LEGO_OT_drag_brick(AssetRefProps, bpy.types.Operator):
    """Drag this brick into the scene"""
    bl_idname = "lego.drag_brick"
    bl_label = "Drag Brick"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return asset_file(context) is not None

    def invoke(self, context, event):
        file = asset_file(context)
        self.region, self.rv3d = view3d_window(context)
        if self.region is None:
            return {'CANCELLED'}

        self.obj = new_brick(context, file)
        if self.obj is None:
            self.report({'WARNING'}, f"Couldn't build {file}")
            return {'CANCELLED'}

        self.rot = 0                       # quarter turns about Z
        self.lift = bottom_offset(self.obj)
        self.update(context, event)

        context.window_manager.modal_handler_add(self)
        context.window.cursor_modal_set('HAND')
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            self.update(context, event)

        elif event.type in {'R', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} and event.value == 'PRESS':
            self.rot += -1 if event.type == 'WHEELDOWNMOUSE' else 1
            self.update(context, event)

        # Drag ends on release; a press also places, for click-move-click use
        elif event.type == 'LEFTMOUSE' and event.value in {'RELEASE', 'PRESS'}:
            if self.mouse_over_view(event):
                return self.finish(context)
            return self.cancel_drag(context)     # dropped back on the shelf

        elif event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self.cancel_drag(context)

        return {'RUNNING_MODAL'}

    # -- placement ----------------------------------------------------------

    def mouse_over_view(self, event):
        r = self.region
        return (r.x <= event.mouse_x < r.x + r.width
                and r.y <= event.mouse_y < r.y + r.height)

    def update(self, context, event):
        hit = self.surface_point(context, event)
        if hit is None:
            return
        if not event.ctrl:
            hit = Vector((round(hit.x / GRID) * GRID,
                          round(hit.y / GRID) * GRID,
                          round(hit.z / PLATE) * PLATE))
        self.obj.matrix_world = (
            Matrix.Translation(hit + Vector((0, 0, self.lift)))
            @ Matrix.Rotation(self.rot * math.pi / 2, 4, 'Z'))

    def surface_point(self, context, event):
        """Point under the mouse on the scene (ignoring the dragged brick),
        else on the ground plane z=0."""
        coord = (event.mouse_x - self.region.x, event.mouse_y - self.region.y)
        origin = view3d_utils.region_2d_to_origin_3d(self.region, self.rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(self.region, self.rv3d, coord)

        depsgraph = context.evaluated_depsgraph_get()
        start = origin
        for _ in range(16):                       # step past hits on our own brick
            ok, loc, _n, _i, hit_obj, _m = context.scene.ray_cast(depsgraph, start, direction)
            if not ok:
                break
            if hit_obj.original != self.obj:
                return loc
            start = loc + direction * 1e-3

        return geometry.intersect_line_plane(
            origin, origin + direction, Vector((0, 0, 0)), Vector((0, 0, 1)))

    # -- end ----------------------------------------------------------------

    def finish(self, context):
        context.window.cursor_modal_restore()
        return {'FINISHED'}

    def cancel_drag(self, context):
        context.window.cursor_modal_restore()
        bpy.data.objects.remove(self.obj, do_unlink=True)
        return {'CANCELLED'}


classes = (
    LEGO_OT_add_brick,
    LEGO_OT_drag_brick,
    LEGO_AST_brick_shelf,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
