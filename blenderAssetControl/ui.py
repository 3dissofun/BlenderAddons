import bpy
from bpy.props import StringProperty, EnumProperty, BoolProperty, CollectionProperty, IntProperty, PointerProperty

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
        layout.operator("op.commit")
        layout.operator("op.diff")
        
classes = (AC_PT_AssetControl,)

def register():
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
