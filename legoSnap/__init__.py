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

from . import props, operators, ui, browser, importModel, exportLxfml

def register():
    props.register()
    browser.register()
    operators.register()
    ui.register()
    importModel.register()
    exportLxfml.register()

def unregister():
    exportLxfml.unregister()
    importModel.unregister()
    operators.unregister()
    ui.unregister()
    browser.unregister()
    props.unregister()
