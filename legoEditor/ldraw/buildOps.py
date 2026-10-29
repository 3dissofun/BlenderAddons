import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

import time
import random
from pathlib import Path

from .importer import importer, library
from .. import utils

class LEGO_OT_ImportRandom(bpy.types.Operator):
    bl_idname = "lego.import_random"
    bl_label = "Import Random Brick"
    bl_options = {'REGISTER','UNDO'}

    def execute(self,context):
        start = time.perf_counter()
        wm = bpy.context.window_manager

        sf = utils.getPref("scaleFactor")
        libPath = library.getPartLibrary()
        if libPath is None:
            self.report({'WARNING'},"No piece library set!")
            return {'CANCELLED'}

        brickIds = library.getAllParts()       
        brickId = random.choice(brickIds)
        obj = importer.getBrickMesh(brickId)
        if obj:
            duration = round(time.perf_counter() - start,4)*1000
            self.report({'INFO'},f"Brick built in {duration}ms")
            actualObj = bpy.data.objects.new("wow",obj)
            actualObj.is_lego = True
            context.scene.collection.objects.link(actualObj)
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
        sf = utils.getPref("scaleFactor")
        if not brickId:
            self.report({'WARNING'},"Please provide a brick ID")
            return {'CANCELLED'}

        obj = importer.getBrickMesh(brickId)
        if obj:
            duration = round(time.perf_counter() - start,4)*1000
            self.report({'INFO'},f"Brick built in {duration}ms")
            actualObj = bpy.data.objects.new("wow",obj)
            actualObj.is_lego = True
            context.scene.collection.objects.link(actualObj)
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

def objectMenu(self, context):
    # 3D Viewport > Object menu; greyed out unless the active object is a brick
    self.layout.separator()
    self.layout.operator(LEGO_OT_DebugSnaps.bl_idname, icon='CONSOLE')

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(importMenu)
    bpy.types.VIEW3D_MT_object.append(objectMenu)

def unregister():
    bpy.types.VIEW3D_MT_object.remove(objectMenu)
    bpy.types.TOPBAR_MT_file_import.remove(importMenu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
