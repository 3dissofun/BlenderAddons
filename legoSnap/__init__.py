import bpy

bl_info = {
    "name": "Lego Environment",
    "author": "Joshua Palfrey",
    "version": (1, 0, 0),
    "blender": (5, 2, 0),
    "location": "View3d -> Lego",
    "description": ("Lego tools for blender"),
    "category": "Modelling",
}

from . import operators, ui 

def register():
    operators.register()
    ui.register()

def unregister():
    operators.unregister()
    ui.unregister()
