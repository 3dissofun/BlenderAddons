import bpy

from . import brickBuilder

class LE_OT_ImportBrick(bpy.types.Operator):
    bl_idname = "lego.import_brick"
    bl_label = "Import Brick"
    bl_options = {'REGISTER'}

    def execute(self,context):
        wm = bpy.context.window_manager
        brickId = wm.brick_id
        if not brickId:
            self.report({'WARNING'},"Please provide a brick ID")
            return {'CANCELLED'}

        obj = brickBuilder.makeBrick(brickId)
        context.scene.collection.objects.link(obj)
        return {'FINISHED'}

class LE_OT_SnapBuild(bpy.types.Operator):
    bl_idname = "lego.snap_build"
    bl_label = "Snap Build"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        self.report({'INFO'},"Build mode active!")
        print("Build mode active!")
        
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            print("MouseMove")
            pass

        elif event.type == "LEFTMOUSE" and event.value == "PRESS":
            self.report({'INFO'},"Mouse Click")
            print("Mouse click")
            return {"RUNNING_MODAL"}
        
        elif event.type in {"RET","ESC", "NUMPAD_ENTER"}:
            self.report({'INFO'},"Build mode quit")
            print("Build mode quit")

            return {"FINISHED"}
 
        return {"RUNNING_MODAL"}

classes = (LE_OT_SnapBuild,LE_OT_ImportBrick)

def register():
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
