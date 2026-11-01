import bpy
from bpy_extras import view3d_utils

from . import utils

class LEGO_OT_SelectPoint(bpy.types.Operator):
    bl_idname = "op.select_points"
    bl_label = "Select Point Instances"

    def invoke(self,context,event):
        context.window_manager.modal_handler_add(self)
        print("started")
        return {"RUNNING_MODAL"}

    def modal(self,context,event):
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            print("stopped")
            return {'CANCELLED'}
        
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            region = context.region
            mouseX = event.mouse_region_x
            mouseY = event.mouse_region_y
            if 0 <= mouseX < region.width and 0 <= mouseY < region.height:
                # if the mouse is actually in the region
                # perform the raycast
                rv3d = context.region_data
                mouseCoord = (mouseX,mouseY)

                rayDir = view3d_utils.region_2d_to_vector_3d(region,rv3d,mouseCoord)
                rayOrigin = view3d_utils.region_2d_to_origin_3d(region,rv3d,mouseCoord)
                depsgraph = context.evaluated_depsgraph_get()

                hit, loc, normal, idx, obj, matrix = context.scene.ray_cast(depsgraph, rayOrigin, rayDir)

        return {'PASS_THROUGH'}

def menuFunc(self,context):
    self.layout.operator(LEGO_OT_SelectPoint.bl_idname)

def register():
    bpy.utils.register_class(LEGO_OT_SelectPoint)
    bpy.types.VIEW3D_MT_view.append(menuFunc)
 
def unregister():
    bpy.types.VIEW3D_MT_view.remove(menuFunc)
    bpy.utils.unregister_class(LEGO_OT_SelectPoint)

if __name__ == "__main__":
    # Clean up first so running the script more than once doesn't add duplicate menu entries.
    try:
        unregister()
    except Exception:
        pass
    register()
