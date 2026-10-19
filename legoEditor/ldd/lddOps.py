import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from pathlib import Path

from . import lxfmlParser
from ..ldraw import importer

class LEGO_OT_ImportLxfml(bpy.types.Operator, ImportHelper):
    bl_idname = "lego.import_lxfml"
    bl_label = "Import LXFML"
    bl_description = "Import a LEGO Digital Designer (.lxfml/.lxf) model"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".lxfml"
    filter_glob: StringProperty(default="*.lxfml;*.lxf", options={'HIDDEN'})

    def execute(self, context):
        # first convert to LDR so works in the ldraw parser as is
        # if slow, could do an in memory return of ldr file and import that
        selectedPath = Path(self.filepath)
        selectedName = selectedPath.stem
        selectedDir = selectedPath.parent

        coll = bpy.data.collections.new(selectedName)
        context.scene.collection.children.link(coll)

        ldrFile = selectedPath.with_suffix(".ldr")
        lxfmlParser.lddToLdr(self.filepath,ldrFile)
        objs = importer.importLdr(ldrFile,coll)
        self.report({'INFO'}, f"Imported {len(objs)} bricks into '{coll.name}'")
        return {'FINISHED'}

classes = (LEGO_OT_ImportLxfml,)

def importMenu(self, context):
    self.layout.operator(LEGO_OT_ImportLxfml.bl_idname, text="LEGO Digital Designer (.lxfml)")

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(importMenu)

def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(importMenu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
