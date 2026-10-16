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

class LEGO_OT_DebugSnaps(bpy.types.Operator):
    bl_idname = "lego.debug_snaps"
    bl_label = "Debug Lego Snaps"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None
                and obj.type == 'MESH'
                and obj.is_lego
                and hasattr(obj.data, "lego_snaps"))

    def execute(self, context):
        obj = context.active_object
        snaps = obj.data.lego_snaps
        if len(snaps) == 0:
            self.report({'WARNING'}, f"'{obj.name}' has no snaps")
            return {'CANCELLED'}

        print(f"=== {obj.name}: {len(snaps)} snaps ===")
        for i, s in enumerate(snaps):
            print(f"--- snap {i} ---")
            for p in s.bl_rna.properties:
                if p.identifier == "rna_type":
                    continue
                v = getattr(s, p.identifier)
                if p.identifier == "secs":
                    v = [(x.shape, x.radius, x.length) for x in v]
                elif p.identifier == "matrix":
                    v = [round(x, 4) for x in v]
                print(f"  {p.identifier}: {v}")

        self.report({'INFO'}, f"Printed {len(snaps)} snaps to console")
        return {'FINISHED'}

classes = (LEGO_OT_ImportBrick, LEGO_OT_ImportRandom, LEGO_OT_DebugSnaps)

def menuDebugSnaps(self, context):
    self.layout.operator(LEGO_OT_DebugSnaps.bl_idname)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_object.append(menuDebugSnaps)

def unregister():
    bpy.types.VIEW3D_MT_object.remove(menuDebugSnaps)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
