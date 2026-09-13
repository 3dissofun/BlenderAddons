import bpy

bl_info = {
    "name": "Asset Control",
    "author": "Joshua Palfrey",
    "version": (1, 0, 0),
    "blender": (5, 2, 0),
    "location": "Collecion -> Asset Control",
    "description": ("Blender asset version control"),
    "category": "Pipeline",
}

from . import props, operators, ui 

def register():
    props.register()
    operators.register()
    ui.register()

def unregister():
    props.unregister()
    operators.unregister()
    ui.unregister()
