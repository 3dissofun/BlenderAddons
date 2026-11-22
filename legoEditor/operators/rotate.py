import bpy

from . import common

def writeRotations(pc, quats):
    attr = pc.attributes.get("rotation")
    if attr is None:
        attr = pc.attributes.new("rotation", 'QUATERNION', 'POINT')
    attr.data.foreach_set("value", quats.astype(np.float32).ravel())
    pc.update_tag()

# Operater itself \/

class LEGO_OT_SelectInstance(bpy.types.Operator):
    bl_idname = "lego.rotate_instances"
    bl_label = "Rotate Instance"
    bl_options = {'REGISTER','UNDO'}

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
