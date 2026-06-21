import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty

from .src.blenderWrapper import exportAdb, importAdb

import os
import time 
import traceback

class AD_OT_Export(bpy.types.Operator, ExportHelper):
    bl_idname = "export.adb"
    bl_label = "Export as Animation Data Binary(.adb)"
    bl_options = {'PRESET'}

    filename_ext = ".adb"
    filter_glob: StringProperty(default="*.adb",options={'HIDDEN'})

    selectionOnly: BoolProperty(name = "Selection Only",default = False)
    compressionType: EnumProperty(name = "Compression Algorithm",description = "Chose between compression methods",
                                  items = [("zlib","Balanced","A balance between speed and file size"),
                                           ("lzma","Agressive","Max compression, slowest"),
                                           ("None","None","Yolo")],
                                  default = "zlib")

    doStatic: BoolProperty(name = "Export Static Values",default = True)

    def execute(self,context):
        if self.selectionOnly:
            objList = list(context.selected_objects)
        else:
            objList = list(context.scene.objects)

        if not self.filepath:
            self.report({'WARNING'},"Invalid filepath. Aborting.")
            return {'CANCELLED'}
        
        options = {"compression":self.compressionType,"static":self.doStatic}

        try:
            start = time.perf_counter()
            result = exportAdb(context,objList,self.filepath,options)
            end = time.perf_counter()

            if result == 0:
                duration = round(((end - start)*1000),2) # ms
                print(f"adb export completed in {duration}ms")
                self.report({'INFO'},f"adb Export took {duration}ms")
                return {'FINISHED'}
            
            else:
                self.report({'ERROR'},f"Exporter exited with code {result}. Check console for details")
                return {'CANCELLED'}
        
        except Exception as e:
            self.report({'ERROR'},f"Exporter failed with unexpected error: {e}. Check console for details.")
            traceback.print_exc()
            return {'CANCELLED'}

def menuExport(self,context):
    self.layout.operator(AD_OT_Export.bl_idname, text ="Animation Data Binary (.adb)")

class AD_OT_Import(bpy.types.Operator, ImportHelper):
    bl_idname = "import.adb"
    bl_label = "Import an Animation Data Binary(.adb) file"
    bl_options = {'PRESET'}

    filename_ext = ".adb"
    filter_glob: StringProperty(default="*.adb",options={'HIDDEN'})

    selectionOnly: BoolProperty(name = "Selection Only.",default = False)
    
    def execute(self,context):
        if self.selectionOnly:
            objList = list(context.selected_objects)
        else:
            objList = list(context.scene.objects)

        if not self.filepath:
            self.report({'WARNING'},"Invalid filepath. Aborting.")
            return {'CANCELLED'}

        try:
            start = time.perf_counter()
            result = importAdb(context,objList,self.filepath)
            end = time.perf_counter()

            if result == 0:
                duration = round(((end - start)*1000),2) # ms
                print(f"adb import completed in {duration}ms")
                self.report({'INFO'},f"adb Import took {duration}ms")
                return {'FINISHED'}
            
            else:
                self.report({'ERROR'},f"Importer exited with code {result}. Check console for details")
                return {'CANCELLED'}
        
        except Exception as e:
            self.report({'ERROR'},f"Importer failed with unexpected error: {e}. Check console for details.")
            traceback.print_exc()
            return {'CANCELLED'}

def menuImport(self,context):
    self.layout.operator(AD_OT_Import.bl_idname, text ="Animation Data Binary (.adb)")

classes = (AD_OT_Export,AD_OT_Import)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(menuExport)
    bpy.types.TOPBAR_MT_file_import.append(menuImport)

def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menuExport)
    bpy.types.TOPBAR_MT_file_import.remove(menuImport)
    for cls in classes:
        bpy.utils.unregister_class(cls)
