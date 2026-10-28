import math
import traceback

import bpy
from mathutils import Matrix

from .base import ModalTool
from . import snapmatch

ROTATE_STEP = math.radians(90)

# key -> (local axis, direction)
ROTATE_KEYS = {
    "W": ("X", -1),
    "S": ("X", 1),
    "D": ("Z", -1),
    "A": ("Z", 1),
}


def stepMatrix(axis, direction, angle=ROTATE_STEP):
    """A rotation to right-multiply onto a world matrix (rotates about its own axes)."""
    return Matrix.Rotation(angle * (1 if direction >= 0 else -1), 4, axis)


def legoBricks(context):
    return [o for o in context.selected_objects if o.is_lego]


class LEGO_OT_move(ModalTool, bpy.types.Operator):
    """Move bricks with the mouse, snapping onto other bricks. Hold Ctrl to move freely"""
    bl_idname = "lego.move"
    bl_label = "Move Brick"
    bl_options = {'REGISTER', 'UNDO'}

    def begin(self, context, event):
        # Grab point: the active brick's origin if it's one of the targets (there
        # may be no active object at all), otherwise the first selected brick
        active = context.active_object
        anchor = active if (active is not None and active in self.targets) else self.targets[0]
        self.pivot = anchor.matrix_world.translation.copy()
        self.grabOffset = self.pivot - self.mouseToWorld(context, event, self.pivot)

        # What update() translates from. Rotations accumulate here, while
        # self.saved keeps the originals so cancelling restores everything.
        self.base = dict(self.saved)

        # Snaps of every other visible brick, gathered once in world space
        self.snapper = snapmatch.Snapper(context, self.targets)
        return True

    def update(self, context, event):
        delta = (self.mouseToWorld(context, event, self.pivot) + self.grabOffset) - self.pivot
        # A snap is a whole world transform: it may tilt the bricks to line their
        # snap up with the target's, as well as moving them (see snapmatch.py)
        snap = None if event.ctrl else self.snapper.find(context, self.base, delta)
        for obj, matrix in self.base.items():
            if snap is not None:
                obj.matrix_world = snap @ matrix
            else:
                m = matrix.copy()
                m.translation = matrix.translation + delta
                obj.matrix_world = m
        context.area.tag_redraw()

    def keyPress(self, context, event):
        if event.type in {"LEFT_CTRL", "RIGHT_CTRL"}:
            self.update(context, event)  # drop the snap straight away, not on the next move
            return True
        step = ROTATE_KEYS.get(event.type)
        if step is None:
            return False
        rot = stepMatrix(*step)
        # Right-multiplying rotates about the brick's own axes and keeps its position
        self.base = {obj: matrix @ rot for obj, matrix in self.base.items()}
        self.update(context, event)  # key events carry the mouse position too
        return True


class LEGO_OT_rotateStep(bpy.types.Operator):
    """One 90 degree step about the bricks' own axes, outside of a move."""
    bl_idname = "lego.rotate_step"
    bl_label = "Rotate Brick 90"
    bl_options = {'REGISTER', 'UNDO'}

    axis: bpy.props.EnumProperty(
        name="Axis",
        items=[('X', "X", "Local X"), ('Y', "Y", "Local Y"), ('Z', "Z", "Local Z")],
        default='X',
    )
    direction: bpy.props.IntProperty(name="Direction", description="+1 or -1", default=1)
    angle: bpy.props.FloatProperty(name="Angle", subtype='ANGLE', default=ROTATE_STEP)

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def execute(self, context):
        try:
            bricks = legoBricks(context)
            if not bricks:
                return {'CANCELLED'}  # nothing selected: skip silently

            rot = stepMatrix(self.axis, self.direction, self.angle)
            for obj in bricks:
                obj.matrix_world = obj.matrix_world @ rot
            context.area.tag_redraw()
            return {'FINISHED'}
        except Exception:
            traceback.print_exc()
            return {'CANCELLED'}


classes = (LEGO_OT_move, LEGO_OT_rotateStep)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
