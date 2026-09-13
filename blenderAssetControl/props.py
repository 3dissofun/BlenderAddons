import bpy

class AC_AP_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    remoteDir: bpy.props.StringProperty(name="Remote", subtype='DIR_PATH',default="C:/Users/Josh/Desktop/remoteRepo/")

    def draw(self,context):
        layout = self.layout
        layout.prop(self,"remoteDir",text="Remote Asset Repository")

def register():
    bpy.utils.register_class(AC_AP_Preferences)
    if not hasattr(bpy.types.ID,"uuid"):
        bpy.types.ID.uuid = bpy.props.StringProperty(name="UUID",description="Stable unique identifier for block tracking",default="",)
    if not hasattr(bpy.types.ID,"last_hash"):
        bpy.types.ID.last_hash = bpy.props.StringProperty(name="LastHash",description="Last computed hash for datablock",default="",)

def unregister():
    bpy.utils.unregister_class(AC_AP_Preferences)
    # NOTE[Josh] I do not delete the uuid or last_hash props incase the addon is registered they should
    # stay consistent
