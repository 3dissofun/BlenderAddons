import bpy

import os

from . import versionControl
from .utils import getRepoDir

class AC_OT_Push(bpy.types.Operator):
    bl_idname = "op.push"
    bl_label = "Push"
    bl_options = {'REGISTER'}

    def execute(self,context):
        self.report({'INFO'},"Push Starting")
        
        coll = context.collection
        if not coll:
            self.report({'ERROR'},"No collection found to push")
            return {'CANCELLED'}

        versionControl.push(coll)

        return {'FINISHED'}

class AC_OT_Commit(bpy.types.Operator):
    bl_idname = "op.commit"
    bl_label = "Commit"
    bl_options = {'REGISTER'}

    def execute(self,context):
        self.report({'INFO'},"Commiting...")
        
        coll = context.collection
        if not coll:
            self.report({'ERROR'},"No collection found to commit")
            return {'CANCELLED'}

        #versionControl.commit(coll)

        return {'FINISHED'}

class AC_OT_Diff(bpy.types.Operator):
    bl_idname = "op.diff"
    bl_label = "Diff"
    bl_options = {'REGISTER'}

    def execute(self,context):
        self.report({'INFO'},"Diff starting")
        
        coll = context.collection
        if not coll:
            self.report({'ERROR'},"No collection found to diff")
            return {'CANCELLED'}

        results = versionControl.diff(coll)
        if not results:
            return {'CANCELLED'}

        coll.ac_datablock_status.clear()
        for status,items in results.items():
            if items:
                for datablock in items:
                    print(f"{status}:{datablock}")
                    entry = coll.ac_datablock_status.add()
                    entry.name = datablock
                    entry.status = status

        return {'FINISHED'}

class AC_OT_Pull(bpy.types.Operator):
    bl_idname = "op.pull"
    bl_label = "Pull"
    bl_options = {'REGISTER'}

    def execute(self,context):
        self.report({'INFO'},"Pull starting")
        
        coll = context.collection
        if not coll:
            self.report({'ERROR'},"No collection found to pull to")
            return {'CANCELLED'}

        return {'FINISHED'}

class AC_OT_FindAssets(bpy.types.Operator):
    bl_idname = "op.find_assets"
    bl_label = "Find assets in repository"

    def execute(self, context):
        scene = context.scene
        repoDir = getRepoDir()
        scene.ac_available_assets.clear()

        if not repoDir:
            self.report({'WARNING'}, "Directory not found")
            return {'FINISHED'}

        for f in repoDir.iterdir():
            if f.is_file() and f.suffix.lower() == '.blend':
                item = scene.ac_available_assets.add()
                item.name = f.stem
                item.filepath = str(f)

        return {'FINISHED'}

class AC_OT_ImportAsset(bpy.types.Operator):
    bl_idname = "op.import_asset"
    bl_label = "Import Asset"
    bl_description = "Import the selected asset"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        assets = scene.ac_available_assets
        index = scene.ac_available_asset_index

        if index < 0 or index >= len(assets):
            self.report({'WARNING'}, "No asset selected")
            return {'CANCELLED'}

        item = assets[index]
        filepath = item.filepath
        collName = os.path.splitext(os.path.basename(filepath))[0]

        if not os.path.isfile(filepath):
            self.report({'WARNING'}, f"File not found: {filepath}")
            return {'CANCELLED'}

        with bpy.data.libraries.load(filepath) as (data_from, data_to):
            if collName not in data_from.collections:
                self.report({'WARNING'}, f"No collection named '{collName}' in {filepath}")
                return {'CANCELLED'}
            data_to.collections = [collName]

        appendedColl = data_to.collections[0]

        if appendedColl is None:
            self.report({'ERROR'}, "Failed to append collection")
            return {'CANCELLED'}

        context.scene.collection.children.link(appendedColl)
        self.report({'INFO'}, f"Imported collection '{collName}'")
        return {'FINISHED'}

classes = (AC_OT_Commit, AC_OT_Diff, AC_OT_Push, AC_OT_Pull, AC_OT_FindAssets, AC_OT_ImportAsset)

def register():
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
