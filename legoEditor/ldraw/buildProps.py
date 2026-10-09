import bpy

def register():
    bpy.types.WindowManager.brick_id = bpy.props.StringProperty(
        name="Brick ID",
        description="ID of the brick to import",
        default="",
    )

def unregister():
    del bpy.types.WindowManager.brick_id
