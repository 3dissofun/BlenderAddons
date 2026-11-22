"""
The intent of this file is to provide an operator that sets the LDraw colour code of the
selected bricks in a lego point cloud.
"""
import bpy
import numpy as np

from . import common

def writePointAttr(pc, name, dataType, values):
    # write a whole INT or BOOLEAN point attribute, creating it if needed
    attr = pc.attributes.get(name)
    if attr is None:
        attr = pc.attributes.new(name, dataType, 'POINT')
    attr.data.foreach_set("value", values)

def readPointAttr(pc, name, dtype, default):
    # a whole point attribute as a numpy array, or default everywhere if it doesn't exist
    vals = np.full(len(pc.points), default, dtype=dtype)
    attr = pc.attributes.get(name)
    if attr is not None:
        attr.data.foreach_get("value", vals)
    return vals

def setColour(pc, mask, code):
    # set the colour code on the points in mask, returns how many changed
    # a cloud with codes but no colExists has a colour on every brick already
    hadCols = pc.attributes.get("col") is not None
    cols = readPointAttr(pc, "col", np.int32, 16)
    exists = readPointAttr(pc, "colExists", bool, hadCols)
    cols[mask] = code
    exists[mask] = True
    writePointAttr(pc, "col", 'INT', cols)
    writePointAttr(pc, "colExists", 'BOOLEAN', exists)
    pc.update_tag()
    return int(mask.sum())

# Operator itself \/

class LEGO_OT_SetColour(bpy.types.Operator):
    """Set the colour code of the selected bricks"""
    bl_idname = "lego.set_colour"
    bl_label = "Set Brick Colour"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return common.pollLegoEdit(context)

    def execute(self, context):
        # the scene's colour unless the caller set one (the redo panel sets it too)
        self.code = context.scene.lego_active_col

        changed = 0
        for obj in context.objects_in_mode:
            if not common.isLegoPointCloud(obj) or len(obj.data.points) == 0:
                continue
            mask = common.readSelection(obj.data)
            if mask.any():
                changed += setColour(obj.data, mask, self.code)

        if changed == 0:
            self.report({'WARNING'}, "No bricks selected")
            return {'CANCELLED'}
        context.area.tag_redraw()
        return {'FINISHED'}

# Registration \/

def register():
    bpy.utils.register_class(LEGO_OT_SetColour)

def unregister():
    bpy.utils.unregister_class(LEGO_OT_SetColour)
