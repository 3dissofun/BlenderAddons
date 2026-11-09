"""
The intent of this file is to provide an override to the select operator when on a point cloud to raycast and select real bricks
"""
import bpy
from bpy_extras import view3d_utils
from bpy.props import BoolProperty, IntVectorProperty

from mathutils.kdtree import KDTree
from mathutils import Quaternion

from .. import utils

def isLegoPointCloud(obj):
    if obj is None:
        return False
    if obj.type == "POINTCLOUD" and getattr(obj,"is_lego_model",False):
        return True
    return False

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

def getHitType(obj):
    # prototypes are named with a 5 digit type prefix, e.g. "00005"
    if obj is None:
        return None
    try:
        return int(obj.original.name[:5])
    except ValueError:
        return None

def readAttr(pointCloud, name, width):
    attr = pointCloud.attributes.get(name)
    if attr is None:
        return None
    vals = [0] * (len(pointCloud.points) * width)
    key = "value"
    attr.data.foreach_get(key, vals)
    return vals

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
        if not isLegoPointCloud(pcObj) or len(pcObj.data.points) == 0:
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
        types = readAttr(pc, "type", 1)
        if types is not None and hitType is not None:
            byType = [i for i in candidates if types[i] == hitType]
            candidates = byType or candidates

        # narrow by rotation (q and -q are the same rotation, hence abs)
        rots = readAttr(pc, "rotation", 4)
        if rots is not None and len(candidates) > 1:
            byRot = [i for i in candidates
                     if abs(Quaternion(rots[i*4:i*4+4]).dot(hitRot)) > 0.9999]
            candidates = byRot or candidates

        # still tied = genuinely identical duplicates; cycle through them
        if len(candidates) > 1:
            sel = readAttr(pc, ".selection", 1)
            if sel is not None:
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
    def poll(self,context):
        # only hijack clicks in point cloud edit mode, in the 3D viewport,
        # when at least one lego cloud is being edited. Returning False lets
        # the keymap fall through to Blender's normal select.
        if context.mode != 'EDIT_POINTCLOUD':
            return False
        if context.area is None or context.area.type != 'VIEW_3D':
            return False
        return any(isLegoPointCloud(o) for o in context.objects_in_mode)

    def invoke(self, context, event):
        mousePos = (event.mouse_region_x, event.mouse_region_y)
        self.location = mousePos
        if raycastAndSelect(context, mousePos, extend=self.extend):
            return {'FINISHED'}
        # nothing hit: let the click go on to the default select operator
        return {'PASS_THROUGH'}

# Keymap \/
 
addon_keymaps = []
 
def registerKeymap():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:  # background mode
        return
 
    # point cloud edit mode keymap; mode keymaps run before the generic "3D View" one
    km = kc.keymaps.new(name="Point Cloud", space_type='EMPTY')
 
    kmi = km.keymap_items.new(LEGO_OT_SelectInstance.bl_idname, 'LEFTMOUSE', 'CLICK')
    kmi.properties.extend = False
    addon_keymaps.append((km, kmi))
 
    kmi = km.keymap_items.new(LEGO_OT_SelectInstance.bl_idname, 'LEFTMOUSE', 'CLICK', shift=True)
    kmi.properties.extend = True
    addon_keymaps.append((km, kmi))
 
def unregisterKeymap():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
 
def register():
    bpy.utils.register_class(LEGO_OT_SelectInstance)
    registerKeymap()
 
def unregister():
    unregisterKeymap()
    bpy.utils.unregister_class(LEGO_OT_SelectInstance)
