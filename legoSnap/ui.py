import bpy

panelName = "Lego"

class LE_PT_LegoPanel(bpy.types.Panel):
    bl_idname = "LE_PT_LegoPanel"
    bl_label = "Lego Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = panelName

    def draw(self,context):
        scene = context.scene
        layout = self.layout
        wm = context.window_manager
        layout.prop(wm,"brick_id")
        layout.operator("lego.import_brick",text="Import Brick", icon='CUBE')
        layout.operator("lego.snap_build",text="Snap Build", icon='MOD_BUILD')

classes = (LE_PT_LegoPanel,)

def register():
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
