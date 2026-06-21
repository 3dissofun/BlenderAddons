bl_info = {
    "name": "AD Animation Exporter",
    "author": "Joshua Palfrey",
    "version": (1, 0, 0),
    "blender": (5, 1, 0),
    "location": "File > Export > Animation Data (.adb)",
    "description": ("Support for Animation Data format (.adb)"),
    "category": "Import-Export",
}

from . import operators

def register():
    operators.register()

def unregister():
    operators.unregister()

if __name__ == "__main__":
    register()
