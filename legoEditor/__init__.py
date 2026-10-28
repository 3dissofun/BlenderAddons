import bpy

bl_info = {
    "name": "Lego Editor",
    "author": "Joshua Palfrey",
    "version": (1, 0, 0),
    "blender": (5, 2, 0),
    "location": "View3d -> Lego",
    "description": ("Lego editor for blender"),
    "category": "Modelling",
}

from . import ldraw, ldd, props, uiOverhaul

def register():
    props.register()
    ldraw.register()
    ldd.register()
    uiOverhaul.register()
    
def unregister():
    ldd.unregister()
    ldraw.unregister()
    uiOverhaul.unregister()
    props.unregister()
