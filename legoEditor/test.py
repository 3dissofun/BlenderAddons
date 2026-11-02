import bpy
from bpy_extras import view3d_utils

from mathutils.kdtree import KDTree

# from . import utils

def buildTree(pointCloud):
    # returns a KD tree for a point cloud input
    # builds from scratch every call
    n = len(pointCloud.points)
    flatPos = [0.0] * (n*3)
    pointCloud.attributes["position"].data.foreach_get("vector",flatPos)

    tree = KDTree(n)
    for i in range(n):
        tree.insert(flatPos[(i*3):(i*3)+3],i)
    tree.balance()
    return tree

def selectPoint(pointCloud, index, extend=False):
    # select a point from a point cloud in edit mode given the index
    # extend means extend the current selection with the new point
    attr = pointCloud.attributes.get(".selection")
    if attr is None:
        attr = pointCloud.attributes.new(".selection", 'BOOLEAN', 'POINT')

    isBoolAttr = attr.data_type == 'BOOLEAN'
    off = False if isBoolAttr else 0.0
    on = True if isBoolAttr else 1.0

    values = [off] * len(pointCloud.points)
    if extend:
        attr.data.foreach_get("value", values)
        values[index] = off if values[index] else on
    else:
        values[index] = on

    attr.data.foreach_set("value", values)
    pointCloud.update_tag()

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
                self.raycastAndSelect(context,event,(mouseX,mouseY))
                return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}

    def raycastAndSelect(self,context,event,mousePos):
        region = context.region
        rv3d = context.region_data

        rayDir = view3d_utils.region_2d_to_vector_3d(region,rv3d,mousePos)
        rayOrigin = view3d_utils.region_2d_to_origin_3d(region,rv3d,mousePos)
        depsgraph = context.evaluated_depsgraph_get()

        hit, loc, normal, idx, obj, matrix = context.scene.ray_cast(depsgraph, rayOrigin, rayDir)
        if not hit:
            return
        
        best = None
        for pcObj in context.objects_in_mode:
            if pcObj.type != 'POINTCLOUD':
                # future additional checks could be made here
                continue
            localHit = pcObj.matrix_world.inverted_safe() @ matrix.translation
            #scaleFac = utils.getModifierInput(pcObj,"LegoInstancer","ScaleFactor")
            scaleFac = None
            if scaleFac is None:
                scaleFac = 1.0
            finalMatrix = localHit / scaleFac

            tree = buildTree(pcObj.data)
            coord, index, dist = tree.find(finalMatrix)

            if (index is not None) and (dist < 0.001) and (best is None or dist < best[2]):
                best = (pcObj,index,dist)

        if best is None:
            return

        pcObj, index, dist = best
        selectPoint(pcObj.data, index)
        context.area.tag_redraw()
        
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
