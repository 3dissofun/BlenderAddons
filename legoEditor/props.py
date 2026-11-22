import bpy
from bpy.props import StringProperty, FloatProperty, BoolProperty, EnumProperty, PointerProperty

import math

from .ldraw.importer import library
from .pointCloud.points import updatePcQuality

def onLibChange(self,context):
    library.clearPartIndex()
    library.clearCaches()

def onShadowLibChange(self,context):
    library.clearCaches()

def onQualityChange(self,context):
    if self.is_lego_model:
        updatePcQuality(self,self.lego_quality)

# Snapping modes for moving bricks in a lego point cloud. Shared by the scene setting
# and the move operator. (identifier, name, description, icon, number)
snapItems = [
    ('GRID', "Grid", "Snap to an LDU grid of Grid Size", 'SNAP_GRID', 0),
    ('BRICK', "Brick", "Snap to other bricks studs, or the grid id none fit", 'MESH_CUBE', 1),
    ('NONE', "None", "No snapping", 'SNAP_OFF', 2),
]

class LE_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    scaleFactor: FloatProperty(name="Scale Factor", default=0.0004)
    pieceLibrary: StringProperty(name="Piece Library",subtype='DIR_PATH',update=onLibChange)
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
    bpy.types.Scene.lego_snap_mode = EnumProperty(
            name="Lego Snapping",
            description="How bricks snap when moved in a lego point cloud",
            items=snapItems,
            default='BRICK',
            )
    bpy.types.Scene.lego_grid_size = FloatProperty(
            name="Grid Size",
            description="Snap step in LDU, for Grid snapping and for Brick snapping when no brick is close",
            default=1.0, min=0.001, soft_max=40.0,
            )
    bpy.types.Scene.lego_rotate_step = FloatProperty(
            name="Rotate Step",
            description="Angle step when rotating bricks with snapping on",
            subtype='ANGLE',
            default=math.radians(90.0), min=math.radians(0.1), max=math.radians(180.0),
            )
    bpy.types.Object.lego_prototypes = PointerProperty(
            name="Prototypes",
            description="Collection holding this model's brick prototypes",
            type=bpy.types.Collection,
            )
    bpy.types.Object.lego_quality = EnumProperty(
            name="Quality",
            description="Brick detail, set with modifiers on the model's prototypes",
            items=[('LOW',"Low","No extra work on the bricks"),('HIGH',"High","Geometry bevels on the bricks")],
            default='LOW',
            update=onQualityChange,
            )
    bpy.utils.register_class(LE_AddonPreferences)

def unregister():
    bpy.utils.unregister_class(LE_AddonPreferences)
    del bpy.types.Scene.lego_grid_size
    del bpy.types.Scene.lego_snap_mode
    del bpy.types.Scene.lego_rotate_step 
    del bpy.types.Object.lego_quality
    del bpy.types.Object.lego_prototypes
