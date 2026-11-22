import bpy
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy_extras.io_utils import ImportHelper

import time
import random
from pathlib import Path

from .importer import importer, library, snapReader
from .. import utils
from ..pointCloud import points

class LEGO_OT_BuildSnaps(bpy.types.Operator):
    bl_idname = "lego.build_snaps"
    bl_label = "Build Snaps"
    bl_description = "Load snapping info onto bricks from the compiled .snap files"
    bl_options = {'REGISTER', 'UNDO'}

    scope: EnumProperty(
            name="Bricks",
            items=[('SELECTED', "Selected", "Selected bricks, and the bricks of selected models"),
                   ('ALL', "All", "Every brick in the file")],
            default='SELECTED')
    rebuild: BoolProperty(name="Rebuild", description="Replace snaps that are already built", default=False)

    def execute(self, context):
        start = time.perf_counter()
        searchObjs = bpy.data.objects
        if self.scope == "SELECTED":
            searchObjs = context.selected_objects
        meshes = [o.data for o in bpy.data.objects if getattr(o,"is_lego",False) and o.type == "MESH"]
        if not meshes:
            self.report({'WARNING'}, "No bricks found")
            return {'CANCELLED'}

        snapReader.buildSnaps(meshes, self.rebuild)
        duration = (time.perf_counter() - start) * 1000
        self.report({'INFO'},f"Snaps built in {duration}ms")
        return {'FINISHED'}

class LEGO_OT_ImportRandom(bpy.types.Operator):
    bl_idname = "lego.import_random"
    bl_label = "Import Random Brick"
    bl_options = {'REGISTER','UNDO'}

    def execute(self,context):
        start = time.perf_counter()
        wm = bpy.context.window_manager

        libPath = library.getPartLibrary()
        if libPath is None:
            self.report({'WARNING'},"No piece library set!")
            return {'CANCELLED'}

        brickIds = library.getAllParts()       
        brickId = random.choice(brickIds)
        
        success = points.importAsPoint(brickId)
        if success:
            duration = round(time.perf_counter() - start,4)*1000
            self.report({'INFO'},f"Brick built in {duration}ms")
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

        success = points.importAsPoint(brickId)
        if success:
            duration = round(time.perf_counter() - start,4)*1000
            self.report({'INFO'},f"Brick built in {duration}ms")
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
    bl_label = "Import LDR/MPD"
    bl_description = "Import a lego (.ldr/.mpd) model"
    bl_options = {'REGISTER','UNDO'}

    filename_ext = ".ldr"
    filter_glob: StringProperty(default="*.ldr;*.mpd", options={'HIDDEN'})
    flattened: BoolProperty(default=True,description="Flatten the the imported model into one object. can help with viewport performance/build previews")

    def draw(self,context):
        layout = self.layout
        layout.prop(self,"flattened")

    def execute(self, context):
        name = Path(self.filepath).stem
        coll = bpy.data.collections.new(name)
        context.scene.collection.children.link(coll)
        sf = utils.getPref("scaleFactor")
        importer.importMpd(self.filepath, coll, scale=sf,flatten=self.flattened)
        return {'FINISHED'}

classes = (LEGO_OT_ImportBrick, LEGO_OT_ImportRandom, LEGO_OT_DebugSnaps, LEGO_OT_ImportLdr, LEGO_OT_BuildSnaps)

def importMenu(self, context):
    self.layout.operator(LEGO_OT_ImportLdr.bl_idname, text="LEGO LDR/MPD file (.ldr/.mpd)")

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
