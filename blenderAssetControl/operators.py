import bpy

from . import versionControl

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
        coll.ac_datablock_status.clear()
        for k,v in results.items():
            if v:
                for i in v:
                    print(f"{k}:{i}")
                    entry = coll.ac_datablock_status.add()
                    entry.name = i
                    entry.status = k


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

classes = (AC_OT_Commit, AC_OT_Diff, AC_OT_Push, AC_OT_Pull)

def register():
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
