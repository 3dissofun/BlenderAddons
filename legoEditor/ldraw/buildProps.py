import bpy

def register():
    bpy.types.WindowManager.brick_id = bpy.props.StringProperty(
        name="Brick ID",
        description="ID of the brick to import",
        default="",
        )
    bpy.types.Object.is_lego = bpy.props.BoolProperty(
        name="Is Lego",
        default=False,
        )

def unregister():
    del bpy.types.WindowManager.brick_id
    del bpy.types.Object.is_lego
