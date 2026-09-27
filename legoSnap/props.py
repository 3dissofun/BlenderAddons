import bpy
from bpy.props import StringProperty, FloatVectorProperty

class LE_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    pieceLibrary: StringProperty(name="Piece Library",subtype='DIR_PATH',default="C:/Users/User/Desktop/pieceLibrary/")
    shadowLibrary: StringProperty(name="Shadow Library",subtype='DIR_PATH',default="C:/Users/User/Desktop/connectorLibrary/")

    def draw(self,context):
        layout = self.layout
        layout.prop(self,"pieceLibrary")

class LE_LegoSnap(bpy.types.PropertyGroup):
    kind: StringProperty() # SNAP_CYL,SNAP_CLP...
    gender: StringProperty() # M or F
    caps: StringProperty()
    secs: StringProperty()
    location: FloatVectorProperty(size=3, subtype='TRANSLATION')
    rotation: FloatVectorProperty(size=4, subtype='QUATERNION', default=(1,0,0,0))

def register():
    bpy.utils.register_class(LE_AddonPreferences)
    bpy.utils.register_class(LE_LegoSnap)
    bpy.types.Mesh.lego_snaps = bpy.props.CollectionProperty(type=LE_LegoSnap)
    bpy.types.WindowManager.brick_id = StringProperty(
        name="Brick ID",
        description="ID of the brick to import",
        default="",
    )

def unregister():
    del bpy.types.WindowManager.brick_id
    del bpy.types.Mesh.lego_snaps
    bpy.utils.unregister_class(LE_AddonPreferences)
    bpy.utils.unregister_class(LE_LegoSnap)
