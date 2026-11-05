import bpy
from bpy.props import StringProperty, FloatProperty, BoolProperty
from .ldraw.importer import library

def onLibChange(self,context):
    library.clearPartIndex()
    library.clearCaches()

def onShadowLibChange(self,context):
    library.clearCaches()

class LE_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    scaleFactor: FloatProperty(name="Scale Factor", default=0.0004)
    pieceLibrary: StringProperty(name="Piece Library",subtype='DIR_PATH',update=onLibChange)
    blenderLdrawLibrary: StringProperty(name="Blender Ldraw Library",subtype='DIR_PATH')
    shadowLibrary: StringProperty(name="Shadow Library",subtype='DIR_PATH',update=onShadowLibChange)

    def draw(self,context):
        layout = self.layout
        layout.prop(self,"scaleFactor")
        layout.prop(self,"pieceLibrary")
        layout.prop(self,"shadowLibrary")
        layout.prop(self,"blenderLdrawLibrary")

def register():
    bpy.types.Object.is_lego_model = BoolProperty(
            name="Is Lego Model",
            description="Marks this point cloud as a Lego model",
            default=False,
            )
    bpy.utils.register_class(LE_AddonPreferences)

def unregister():
    bpy.utils.unregister_class(LE_AddonPreferences)
