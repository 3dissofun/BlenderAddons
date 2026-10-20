import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

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

        sf = bpy.context.preferences.addons[__package__.rpartition(".")[0]].preferences.scaleFactor
        partsPath = Path(library.getPartLibrary()) / "parts"
        if not partsPath.exists():
            self.report({'ERROR'},"No 'parts' subdirectory found in piece library")
            return {'CANCELLED'}

        
        global brickIds
        if not brickIds:
            ranCatalog = partsPath / "catalogRandom.txt"

            # 1. try the cached catalog on disk
            if ranCatalog.is_file():
                try:
                    lines = ranCatalog.read_text(encoding="utf-8").splitlines()
                    brickIds = [l.strip() for l in lines if l.strip()]
                except OSError as e:
                    print(f"LEGO: could not read {ranCatalog}: {e}")

            # 2. no cache (or empty/unreadable): scan the parts folder and save the result
            if not brickIds:
                brickIds = [f.stem for f in partsPath.iterdir() if f.is_file() and f.suffix.lower() == ".dat" and f.stem.isdigit()]
                if not brickIds:
                    self.report({'ERROR'},"No .dat files found in piece library parts subdir")
                    return {'CANCELLED'}
                try:
                    ranCatalog.write_text("\n".join(brickIds), encoding="utf-8")
                except OSError as e:
                    # read-only library etc: still works this session, just not cached
                    print(f"LEGO: could not write {ranCatalog}: {e}")


        brickId = random.choice(brickIds)
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
        sf = bpy.context.preferences.addons[__package__.rpartition(".")[0]].preferences.scaleFactor
        if not brickId:
            self.report({'WARNING'},"Please provide a brick ID")
            return {'CANCELLED'}

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

class LEGO_OT_ImportLdr(bpy.types.Operator, ImportHelper):
    bl_idname = "lego.import_ldr"
    bl_label = "Import LDR"
    bl_description = "Import a lego (.ldr/.mpd) model"
    bl_options = {'REGISTER','UNDO'}

    filename_ext = ".ldr"
    filter_glob: StringProperty(default="*.ldr;*.mpd", options={'HIDDEN'})

    def execute(self, context):
        name = Path(self.filepath).stem
        coll = bpy.data.collections.new(name)
        context.scene.collection.children.link(coll)
        objs = importer.importLdr(self.filepath, coll)
        self.report({'INFO'}, f"Imported {len(objs)} bricks into '{coll.name}'")
        return {'FINISHED'}

classes = (LEGO_OT_ImportBrick, LEGO_OT_ImportRandom, LEGO_OT_DebugSnaps, LEGO_OT_ImportLdr)

def importMenu(self, context):
    self.layout.operator(LEGO_OT_ImportLdr.bl_idname, text="LEGO LDR file (.ldr)")

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(importMenu)

def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(importMenu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
