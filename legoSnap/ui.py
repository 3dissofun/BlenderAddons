import bpy

from . import browser

panelName = "Lego"

class LE_PT_LegoPanel(bpy.types.Panel):
    bl_idname = "LE_PT_LegoPanel"
    bl_label = "Lego Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = panelName

    def draw(self,context):
        layout = self.layout
        wm = context.window_manager

        row = layout.row(align=True)
        row.prop(wm, "brick_search", text="", icon='VIEWZOOM')
        row.operator("lego.refresh_previews", text="", icon='FILE_REFRESH')
        layout.template_icon_view(wm, "brick_browser", show_labels=True, scale=6.0, scale_popup=5.0)
        desc = browser.describe(wm.brick_id)
        if desc:
            layout.label(text=desc)
        layout.prop(wm, "brick_id")
        layout.operator("lego.import_brick", text="Import Brick", icon='CUBE')
        layout.operator("lego.snap_build", text="Snap Build", icon='MOD_BUILD')
        layout.operator("lego.toggle_snaps", text="Toggle Snaps", icon='MOD_BUILD')

classes = (LE_PT_LegoPanel,)

def register():
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
