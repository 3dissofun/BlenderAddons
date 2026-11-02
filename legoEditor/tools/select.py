"""
The intent of this file is to provide functions that provide pointCloudObject and indexOfPoint outputs given a mouse coordinate
A utilty function exists to select the point too, so from a modal operator, that function can be called directly to overwrite blenders
default selections, if on a lego object, else it returns False, in which case the modal operator should PASS_THROUGH and allow blender
to intercept the click
"""
import bpy
from bpy_extras import view3d_utils

from mathutils.kdtree import KDTree

from .. import utils

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

def pickPoint(context, mousePos):
    # raycast to the object under the mouse, if its a lego model point cloud
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return None
    if not (0 <= mousePos[0] < region.width and 0 <= mousePos[1] < region.height):
        return None

    rayDir = view3d_utils.region_2d_to_vector_3d(region, rv3d, mousePos)
    rayOrigin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mousePos)
    depsgraph = context.evaluated_depsgraph_get()

    hit, loc, normal, idx, obj, matrix = context.scene.ray_cast(depsgraph, rayOrigin, rayDir)
    if not hit:
        return None

    best = None
    for pcObj in context.objects_in_mode:
        if pcObj.type != 'POINTCLOUD' or len(pcObj.data.points) == 0:
            # future additional checks could be made here
            continue

        localPos = pcObj.matrix_world.inverted_safe() @ matrix.translation
        scaleFac = utils.getModifierInput(pcObj, "LegoInstancer", "ScaleFactor")
        if not scaleFac:
            scaleFac = 1.0
        query = localPos / scaleFac

        coord, index, dist = buildTree(pcObj.data).find(query)
        if index is not None and dist < 0.001 and (best is None or dist < best[2]):
            best = (pcObj, index, dist)

    if best is None:
        return None
    return best[0], best[1]

def raycastAndSelect(context,event,mousePos):
    # select the point if in edit mode, return True
    # if no lego is hit, return False, this means modals should call PASS_THROUGH on False return
    if mousePos is None:
        mousePos = (event.mouse_region_x, event.mouse_region_y)

    result = pickPoint(context, mousePos)
    if result is None:
        return False

    pcObj, index = result
    selectPoint(pcObj.data, index, extend=event.shift)
    context.area.tag_redraw()
    return True
