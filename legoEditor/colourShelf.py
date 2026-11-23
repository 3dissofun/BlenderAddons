"""
LDraw colour asset shelf for the Lego Editor
"""
import bpy

from .ldraw.col import colors
from .ui import panelName

DESC_PREFIX = "ldraw_colour:"

def assetCode(context):
    asset = getattr(context, "asset", None)
    if asset is None or asset.metadata is None:
        return None
    desc = asset.metadata.description
    if not desc.startswith(DESC_PREFIX):
        return None
    try:
        return int(desc[len(DESC_PREFIX):])
    except ValueError:
        return None

def _library_type_items():
    # Same items as bpy.types.AssetWeakReference.asset_library_type
    prop = bpy.types.AssetWeakReference.bl_rna.properties["asset_library_type"]
    return [(i.identifier, i.name, i.description) for i in prop.enum_items]

class LEGO_OT_pick_colour(bpy.types.Operator):
    """Make this the active brick colour"""
    bl_idname = "lego.pick_colour"
    bl_label = "Pick Colour"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    # the shelf fills these in before running the operator; we read context.asset
    # instead, but they have to exist
    asset_library_type: bpy.props.EnumProperty(
        items=_library_type_items(), options={'HIDDEN', 'SKIP_SAVE'})
    asset_library_identifier: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    relative_asset_identifier: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return assetCode(context) is not None

    def execute(self, context):
        context.scene.lego_active_col = assetCode(context)
        return {'FINISHED'}

class LEGO_AST_colour_shelf(bpy.types.AssetShelf):
    bl_space_type = 'VIEW_3D'
    bl_idname = "VIEW3D_AST_lego_colours"
    bl_options = {'DEFAULT_VISIBLE', 'NO_ASSET_DRAG'}
    bl_activate_operator = "lego.pick_colour"
    bl_default_preview_size = 48

    @classmethod
    def poll(cls, context):
        return True

    @classmethod
    def asset_poll(cls, asset):
        meta = asset.metadata
        return (asset.id_type == 'OBJECT' and meta is not None
                and meta.description.startswith(DESC_PREFIX))

classes = (LEGO_OT_pick_colour, LEGO_AST_colour_shelf)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
