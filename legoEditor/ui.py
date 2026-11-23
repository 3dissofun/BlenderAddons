import bpy

panelName = "LEGO"

class LEGO_PT_LegoDebugPanel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = panelName
    bl_label = "Debug"

    def draw(self,context):
        layout = self.layout
        layout.operator("lego.import_random")
        layout.operator("lego.build_snaps")

class LEGO_PT_LegoPanel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = panelName
    bl_label = "Bricks"

    def draw(self,context):
        layout = self.layout
        layout.operator("lego.import_random")
        layout.operator("lego.build_snaps")

        obj = context.active_object
        if obj and obj.is_lego_model:
            layout.prop(obj,"lego_quality",expand=True)

classes = (LEGO_PT_LegoPanel,)

def register():
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
