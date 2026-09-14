import bpy

panelName = "Asset Control"

class AC_UL_DiffList(bpy.types.UIList):
    bl_idname = "AC_UL_DiffList"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name)
        layout.label(text=item.status)

class AC_PT_AssetControl(bpy.types.Panel):
    bl_idname = "AC_PT_asset_control"
    bl_label = "Asset Control"
    
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "collection"
    
    @classmethod
    def poll(cls,context):
        coll = context.collection
        if coll is None:
            return False
        else:
            return True

    def draw(self,context):
        layout = self.layout
        coll = context.collection
        layout.operator("op.remove_asset",text="Delete Collection")
        layout.operator("op.commit")
        layout.operator("op.diff")
        layout.template_list("AC_UL_DiffList", "", coll, "ac_datablock_status", coll, "ac_datablock_index")

class AC_UL_AvailableAssets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon='FILE_BLEND')

class AC_PT_Import(bpy.types.Panel):
    bl_idname = "AC_PT_import"
    bl_label = "Import"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = panelName

    def draw(self,context):
        scene = context.scene
        layout = self.layout
        layout.operator("op.find_assets", icon='FILE_REFRESH')
        layout.template_list(
            "AC_UL_AvailableAssets", "",
            scene, "ac_available_assets",
            scene, "ac_available_asset_index"
        )
        layout.operator("op.import_asset",text="Import", icon='IMPORT')

classes = (AC_UL_AvailableAssets,AC_UL_DiffList,AC_PT_AssetControl,AC_PT_Import)

def register():
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
