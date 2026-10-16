import bpy

import time
import random
from pathlib import Path

from . import importer, library

brickIds = []

class LEGO_OT_ImportRandom(bpy.types.Operator):
    bl_idname = "lego.import_random"
    bl_label = "Import Random Brick"
    bl_options = {'REGISTER','UNDO'}

    def execute(self,context):
        start = time.perf_counter()
        wm = bpy.context.window_manager

        pieceLibrary = bpy.context.preferences.addons[__package__.rpartition(".")[0]].preferences.pieceLibrary
        shadowLibrary = bpy.context.preferences.addons[__package__.rpartition(".")[0]].preferences.shadowLibrary
        sf = bpy.context.preferences.addons[__package__.rpartition(".")[0]].preferences.scaleFactor

        partsPath = Path(pieceLibrary) / "parts"
        if not partsPath.exists():
            self.report({'ERROR'},"No 'parts' subdirectory found in piece library")
            return {'CANCELLED'}

        global brickIds
        if not brickIds:
            brickIds = [f.stem for f in partsPath.iterdir() if f.is_file() and f.suffix.lower() == ".dat" and f.stem.isdigit()]

        if not brickIds:
            self.report({'ERROR'},"No .dat files found in piece library parts subdir")
            return {'CANCELLED'}

        brickId = random.choice(brickIds)
        library.setPartLibrary(pieceLibrary)
        library.setShadowLibrary(shadowLibrary)
        obj = importer.makeBrick(brickId)
        if obj:
            duration = round(time.perf_counter() - start,4)*1000
            self.report({'INFO'},f"Brick built in {duration}ms")
            context.scene.collection.objects.link(obj)
            return {'FINISHED'}
        else:
            self.report({'WARNING'},"No brick found for given ID")
            return {'CANCELLED'}

class LEGO_OT_ImportBrick(bpy.types.Operator):
    bl_idname = "lego.import_brick"
    bl_label = "Import Brick"
    bl_options = {'REGISTER','UNDO'}

    def execute(self,context):
        start = time.perf_counter()
        wm = bpy.context.window_manager
        brickId = wm.brick_id
        pieceLibrary = bpy.context.preferences.addons[__package__.rpartition(".")[0]].preferences.pieceLibrary
        shadowLibrary = bpy.context.preferences.addons[__package__.rpartition(".")[0]].preferences.shadowLibrary
        sf = bpy.context.preferences.addons[__package__.rpartition(".")[0]].preferences.scaleFactor
        if not brickId:
            self.report({'WARNING'},"Please provide a brick ID")
            return {'CANCELLED'}

        library.setPartLibrary(pieceLibrary)
        library.setShadowLibrary(shadowLibrary)
        obj = importer.makeBrick(brickId)
        if obj:
            duration = round(time.perf_counter() - start,4)*1000
            self.report({'INFO'},f"Brick built in {duration}ms")
            context.scene.collection.objects.link(obj)
            return {'FINISHED'}
        else:
            self.report({'WARNING'},"No brick found for given ID")
            return {'CANCELLED'}

classes = (LEGO_OT_ImportBrick, LEGO_OT_ImportRandom)

def register():
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
