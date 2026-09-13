import bpy

def register():
    if not hasattr(bpy.types.ID,"uuid"):
        bpy.types.ID.uuid = bpy.props.StringProperty(name="UUID",description="Stable unique identifier for block tracking",default="",)
    if not hasattr(bpy.types.ID,"last_hash"):
        bpy.types.ID.last_hash = bpy.props.StringProperty(name="LastHash",description="Last computed hash for datablock",default="",)

def unregister():
    # NOTE[Josh] I do not delete the uuid or last_hash props incase the addon is registered they should
    # stay consistent
    pass
