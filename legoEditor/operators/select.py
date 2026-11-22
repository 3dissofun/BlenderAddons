"""
The intent of this file is to provide an override to the select operator when on a point cloud to raycast and select real bricks
"""
import bpy
from bpy_extras import view3d_utils
from bpy.props import BoolProperty, IntVectorProperty

from mathutils.kdtree import KDTree
from mathutils import Quaternion

from .. import utils
from . import common

def buildTree(pointCloud):
    # returns a KD tree for a point cloud input
    # builds from scratch every call
    positions = common.readPositions(pointCloud)
    tree = KDTree(len(positions))
    for i, p in enumerate(positions):
        tree.insert(p, i)
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

def getHitType(obj):
    # the brick type of a hit instance, from its prototype's name
    if obj is None:
        return None
    return common.typeFromName(obj.original.name)

def pickPoint(context, mousePos):
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

    hitType = getHitType(obj)

    for pcObj in context.objects_in_mode:
        if not common.isLegoPointCloud(pcObj) or len(pcObj.data.points) == 0:
            continue
        pc = pcObj.data

        localMat = pcObj.matrix_world.inverted_safe() @ matrix
        scaleFac = utils.getModifierInput(pcObj, "LegoInstancer", "ScaleFactor") or 1.0
        query = localMat.translation / scaleFac
        hitRot = localMat.to_3x3().normalized().to_quaternion()

        candidates = [i for (_, i, _) in buildTree(pc).find_range(query, 0.001)]
        if not candidates:
            continue

        # narrow by brick type
        if hitType is not None:
            types = common.readTypes(pc)
            byType = [i for i in candidates if types[i] == hitType]
            candidates = byType or candidates

        # narrow by rotation (q and -q are the same rotation, hence abs)
        if len(candidates) > 1:
            rots = common.readRotations(pc)
            byRot = [i for i in candidates if abs(Quaternion(rots[i]).dot(hitRot)) > 0.9999]
            candidates = byRot or candidates

        # still tied = genuinely identical duplicates; cycle through them
        if len(candidates) > 1:
            sel = common.readSelection(pc)
            selected = [i for i in candidates if sel[i]]
            if selected:
                last = candidates.index(selected[-1])
                return pcObj, candidates[(last + 1) % len(candidates)]

        return pcObj, candidates[0]

    return None

def raycastAndSelect(context, mousePos, extend=False):
    # select the point if in edit mode, return True
    # if no lego is hit, return False
    result = pickPoint(context, mousePos)
    if result is None:
        return False

    pcObj, index = result
    selectPoint(pcObj.data, index, extend=extend)
    context.area.tag_redraw()
    return True

# Operater itself \/

class LEGO_OT_SelectInstance(bpy.types.Operator):
    bl_idname = "lego.select_instance"
    bl_label = "Select Instance"
    bl_options = {'REGISTER','UNDO'}

    extend: BoolProperty(name="Extend Selection",description="Add to the existing selection")
    location: IntVectorProperty(name="Location",description="Mouse location in region coordinates, internal",size=2,default=(0, 0),options={'HIDDEN', 'SKIP_SAVE'},)

    @classmethod
    def poll(cls, context):
        return common.pollLegoEdit(context)  # otherwise the click falls through to Blender's select

    def invoke(self, context, event):
        mousePos = (event.mouse_region_x, event.mouse_region_y)
        self.location = mousePos
        if raycastAndSelect(context, mousePos, extend=self.extend):
            return {'FINISHED'}
        # nothing hit: let the click go on to the default select operator
        return {'PASS_THROUGH'}

# Registration \/

keymaps = common.Keymaps()

def register():
    bpy.utils.register_class(LEGO_OT_SelectInstance)
    kmi = keymaps.add(LEGO_OT_SelectInstance.bl_idname, 'LEFTMOUSE', 'CLICK')
    if kmi is not None:
        kmi.properties.extend = False
    kmi = keymaps.add(LEGO_OT_SelectInstance.bl_idname, 'LEFTMOUSE', 'CLICK', shift=True)
    if kmi is not None:
        kmi.properties.extend = True

def unregister():
    keymaps.clear()
    bpy.utils.unregister_class(LEGO_OT_SelectInstance)
