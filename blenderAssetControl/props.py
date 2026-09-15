import bpy

class AC_AP_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    remoteDir: bpy.props.StringProperty(name="Remote", subtype='DIR_PATH',default="C:/Users/Josh/Desktop/remoteRepo/")

    def draw(self,context):
        layout = self.layout
        layout.prop(self,"remoteDir",text="Remote Asset Repository")

class AC_PG_DatablockStatus(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()
    status: bpy.props.StringProperty()

class AC_PG_AvailableAsset(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()
    filepath: bpy.props.StringProperty()

def register():
    bpy.utils.register_class(AC_AP_Preferences)
    bpy.utils.register_class(AC_PG_DatablockStatus)
    bpy.utils.register_class(AC_PG_AvailableAsset)
    if not hasattr(bpy.types.ID,"uuid"):
        bpy.types.ID.uuid = bpy.props.StringProperty(name="UUID",description="Stable unique identifier for block tracking",default="",)
    if not hasattr(bpy.types.ID,"last_hash"):
        bpy.types.ID.last_hash = bpy.props.StringProperty(name="LastHash",description="Last computed hash for datablock",default="",)

    # For diff tracking
    bpy.types.Collection.ac_datablock_status = bpy.props.CollectionProperty(type=AC_PG_DatablockStatus)
    bpy.types.Collection.ac_datablock_index = bpy.props.IntProperty()

    # For asset import
    bpy.types.Scene.ac_available_assets = bpy.props.CollectionProperty(type=AC_PG_AvailableAsset)
    bpy.types.Scene.ac_available_asset_index = bpy.props.IntProperty(default=0)

    # For commits
    bpy.types.Collection.ac_commit_message = bpy.props.StringProperty(
        name="Commit Message",
        description="Message describing the changes in this commit",
        default="",
    )

def unregister():
    bpy.utils.unregister_class(AC_PG_AvailableAsset)
    bpy.utils.unregister_class(AC_PG_DatablockStatus)
    bpy.utils.unregister_class(AC_AP_Preferences)

    del bpy.types.Collection.ac_commit_message
    # NOTE[Josh] I do not delete the uuid or last_hash props incase the addon is registered they should
    # stay consistent
