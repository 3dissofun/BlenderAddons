import bpy
from bpy.props import StringProperty, FloatProperty
from .ldraw import library, importer

def onLibChange(self,context):
    library.clearPartIndex()
    library.clearCaches()

def onShadowLibChange(self,context):
    library.clearCaches()

def onSfChange(self,context):
    library.clearCaches()
    importer.setScale(self.scaleFactor)

class LE_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    scaleFactor: FloatProperty(name="Scale Factor", default=0.0004, update=onSfChange)
    pieceLibrary: StringProperty(name="Piece Library",subtype='DIR_PATH',update=onLibChange)
    shadowLibrary: StringProperty(name="Shadow Library",subtype='DIR_PATH',update=onShadowLibChange)

    def draw(self,context):
        layout = self.layout
        layout.prop(self,"scaleFactor")
        layout.prop(self,"pieceLibrary")
        layout.prop(self,"shadowLibrary")

def register():
    bpy.utils.register_class(LE_AddonPreferences)

def unregister():
    bpy.utils.unregister_class(LE_AddonPreferences)
