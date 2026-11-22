"""
The intent of this file is to provide an override to the rotate operator (R) when in a lego point cloud.

The selected bricks turn together about a pivot, and both their positions and their rotation
attribute are updated (via common.CloudMove, the same code the move operator uses for brick snaps).

Snapping follows scene.lego_snap_mode, read when R is pressed:
    GRID / BRICK  the angle snaps to scene.lego_rotate_step (90 degrees by default), and the
                  pivot snaps to the Grid Size in the cloud's local LDU space, so bricks that
                  start on the grid end on it. With no axis locked, the bricks turn about the
                  world axis closest to the view direction rather than the view itself
    NONE          free angle about the view axis, pivot at the middle of the selection

Controls while rotating:
    mouse          rotate around the pivot on screen
    X / Y / Z      turn about a world axis (press again to unlock)
    Ctrl (held)    no snapping (whatever the mode)
    LMB / Enter    confirm
    RMB / Esc      cancel
"""
import math

import bpy
import numpy as np
from bpy_extras import view3d_utils
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty
from mathutils import Matrix, Vector

from . import common

# Maths \/

def wrapAngle(a):
    # into (-pi, pi], so crossing the atan2 seam doesn't jump a whole turn
    return (a + math.pi) % (2 * math.pi) - math.pi

def rotationAbout(pivot, axis, angle):
    # world 4x4 (numpy) turning by angle about axis through pivot
    m = (Matrix.Translation(pivot) @ Matrix.Rotation(angle, 4, axis)
         @ Matrix.Translation(-pivot))
    return np.array(m, dtype=np.float64)

def snapPivot(cloud, pivot, grid):
    # put the pivot on the grid in the cloud's local LDU space
    mat = cloud.obj.matrix_world
    local = mat.inverted_safe() @ pivot
    return mat @ Vector(common.snapToGrid(local, grid))

def nearestWorldAxis(direction):
    # (index, signed unit axis) of the world axis closest to direction
    i = max(range(3), key=lambda a: abs(direction[a]))
    return i, Vector(((1, 0, 0), (0, 1, 0), (0, 0, 1))[i]) * (1.0 if direction[i] >= 0 else -1.0)

def applyRotation(clouds, pivot, axis, angle):
    rot = rotationAbout(Vector(pivot), Vector(axis), angle)
    for cloud in clouds:
        cloud.applyTransform(rot)  # left-multiplies onto each selected brick's world matrix

# Operator itself \/

class LEGO_OT_RotateInstance(bpy.types.Operator):
    """Rotate the selected bricks, in steps of the scene's rotate step. Hold Ctrl to rotate freely"""
    bl_idname = "lego.rotate_instance"
    bl_label = "Rotate Bricks"
    bl_options = {'REGISTER', 'UNDO'}

    angle: FloatProperty(name="Angle", subtype='ANGLE')
    # where and about what the bricks turned, so the redo panel can repeat it
    axis: FloatVectorProperty(size=3, default=(0.0, 0.0, 1.0), options={'HIDDEN', 'SKIP_SAVE'})
    pivot: FloatVectorProperty(size=3, subtype='TRANSLATION', options={'HIDDEN', 'SKIP_SAVE'})
    snapped: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return common.pollLegoEdit(context)  # otherwise R falls through to Blender's rotate

    # mouse maths \/

    def toViewer(self, context):
        # unit vector from the view towards the viewer
        return context.region_data.view_rotation @ Vector((0.0, 0.0, 1.0))

    def mouseAngle(self, context, event):
        # angle of the mouse around the pivot as drawn on screen
        centre = view3d_utils.location_3d_to_region_2d(context.region, context.region_data,
                                                       self.pivotFree)
        if centre is None:  # pivot behind the view
            centre = Vector((context.region.width / 2, context.region.height / 2))
        return math.atan2(event.mouse_region_y - centre.y, event.mouse_region_x - centre.x)

    def currentAxis(self, context, snapping):
        # (world axis, label). Locked: that world axis. Snapping: the world axis nearest the
        # view. Free: the view axis itself, as Blender's R does
        view = self.toViewer(context)
        if self.axisIndex is not None:
            return Vector(((1, 0, 0), (0, 1, 0), (0, 0, 1))[self.axisIndex]), "XYZ"[self.axisIndex]
        if snapping:
            i, axis = nearestWorldAxis(view)
            return axis, "view, nearest " + "XYZ"[i]
        return view, "view"

    # modal \/

    def invoke(self, context, event):
        self.clouds = common.gatherClouds(context)
        if not self.clouds:
            return {'PASS_THROUGH'}  # nothing selected: let Blender's R deal with it

        scene = context.scene
        self.snapOn = scene.lego_snap_mode != 'NONE'
        self.step = scene.lego_rotate_step

        self.pivotFree = common.selectionCentre(self.clouds)
        self.pivotSnapped = snapPivot(self.clouds[0], self.pivotFree, scene.lego_grid_size)

        self.axisIndex = None
        self.lastMouse = self.mouseAngle(context, event)
        self.turned = 0.0  # how far the mouse has gone round the pivot on screen, unwrapped

        context.window_manager.modal_handler_add(self)
        self.update(context, event)
        return {'RUNNING_MODAL'}

    def update(self, context, event):
        a = self.mouseAngle(context, event)
        self.turned += wrapAngle(a - self.lastMouse)
        self.lastMouse = a

        snapping = self.snapOn and not event.ctrl
        axis, axisText = self.currentAxis(context, snapping)
        # anticlockwise on screen is a positive turn about the axis pointing at the viewer
        angle = self.turned if axis.dot(self.toViewer(context)) >= 0 else -self.turned
        if snapping:
            angle = round(angle / self.step) * self.step
        pivot = self.pivotSnapped if snapping else self.pivotFree

        self.angle = angle
        self.axis = axis
        self.pivot = pivot
        self.snapped = snapping
        applyRotation(self.clouds, pivot, axis, angle)

        snapText = f"step {math.degrees(self.step):g}°" if snapping else "free"
        context.area.header_text_set(
            f"Rotate  {math.degrees(angle):.2f}°  about {axisText}  ({snapText})"
            "    X/Y/Z: axis   Ctrl: free   LMB: confirm   RMB: cancel")
        context.area.tag_redraw()

    def setAxis(self, event):
        axis = 'XYZ'.index(event.type)
        self.axisIndex = None if self.axisIndex == axis else axis  # pressing again unlocks

    def modal(self, context, event):
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}:
            return {'PASS_THROUGH'}  # allow zooming while rotating

        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            self.update(context, event)

        elif event.type in {'X', 'Y', 'Z'} and event.value == 'PRESS':
            self.setAxis(event)
            self.update(context, event)

        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'} and event.value == 'PRESS':
            common.clearHeader(context)
            return {'FINISHED'}

        elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            for cloud in self.clouds:
                cloud.restore()
            common.clearHeader(context)
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def execute(self, context):
        # used by the redo panel (F9 / bottom left): repeats the turn from the stored values
        clouds = common.gatherClouds(context)
        if not clouds:
            return {'CANCELLED'}
        if Vector(self.axis).length < 1e-9:
            return {'CANCELLED'}
        applyRotation(clouds, self.pivot, Vector(self.axis).normalized(), self.angle)
        return {'FINISHED'}

    def draw(self, context):
        self.layout.prop(self, "angle")

# Header UI \/

def drawRotateHeader(self, context):
    # the rotate step, next to move.py's snapping mode and grid size
    if not LEGO_OT_RotateInstance.poll(context):
        return
    scene = context.scene
    row = self.layout.row(align=True)
    row.active = scene.lego_snap_mode != 'NONE'  # greyed out, but still editable, when unused
    row.prop(scene, "lego_rotate_step", text="Rotate")

# Registration \/

keymaps = common.Keymaps()

def register():
    bpy.utils.register_class(LEGO_OT_RotateInstance)
    bpy.types.VIEW3D_HT_header.append(drawRotateHeader)
    keymaps.add(LEGO_OT_RotateInstance.bl_idname, 'R', 'PRESS')

def unregister():
    keymaps.clear()
    bpy.types.VIEW3D_HT_header.remove(drawRotateHeader)
    bpy.utils.unregister_class(LEGO_OT_RotateInstance)
