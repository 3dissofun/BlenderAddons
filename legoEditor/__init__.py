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

from . import ldraw, ldd, props
#from . import uiOverhaul
from . import editor

def register():
    props.register()
    ldraw.register()
    ldd.register()
    #uiOverhaul.register()
    editor.register()

def unregister():
    ldd.unregister()
    ldraw.unregister()
    #uiOverhaul.unregister()
    editor.unregister()
    props.unregister()
