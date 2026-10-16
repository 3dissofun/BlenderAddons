"""Shared plumbing for interactive (modal) LEGO tools.

A tool is a normal Blender operator that mixes in ModalTool and implements
begin() / update(). The mixin handles confirm, cancel, undo and navigation
pass-through, so each tool file only contains what is specific to it.

Because tools set the UNDO option, returning FINISHED gives one Ctrl+Z step.
CANCELLED restores every target to its matrix at invoke time.
"""
import traceback

import bpy
from bpy_extras import view3d_utils

NAV_EVENTS = {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE",
              "WHEELOUTMOUSE", "TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE",
              "NDOF_MOTION"}

CONFIRM = {"LEFTMOUSE", "RET", "NUMPAD_ENTER"}
ABORT = {"RIGHTMOUSE", "ESC"}

# Tools currently running. The editor's modal checks isBusy() and passes every
# event through while a tool owns the input, so the tool's own keys (W/A/S/D,
# ESC...) reach it instead of being swallowed by the editor.
_running = set()


ENDED = {'FINISHED', 'CANCELLED'}


def isBusy():
    return bool(_running)


def reset():
    """Safety net: forget any tool that somehow never reported finishing."""
    _running.clear()


class ModalTool:
    bl_options = {'REGISTER', 'UNDO'}

    # ---- helpers for subclasses ----
    @staticmethod
    def mouseToWorld(context, event, depthPoint):
        """Mouse position projected onto the view plane through depthPoint."""
        coord = (event.mouse_region_x, event.mouse_region_y)
        return view3d_utils.region_2d_to_location_3d(
            context.region, context.region_data, coord, depthPoint)

    # ---- hooks for subclasses ----
    def poll_targets(self, context):
        """Objects this tool acts on. Override to filter further."""
        return [o for o in context.selected_objects if o.is_lego]

    def begin(self, context, event):
        """Called once before the modal starts. Return False to abort."""
        return True

    def update(self, context, event):
        """Called on every mouse move."""

    def finish(self, context, event):
        """Called on confirm, before FINISHED is returned."""

    def keyPress(self, context, event):
        """Called on a key press while the tool runs (not confirm/abort/navigation).
        Return True if the key was handled."""
        return False

    # ---- operator plumbing ----
    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        try:
            self.targets = self.poll_targets(context)
            if not self.targets:
                return {'CANCELLED'}  # nothing to act on: skip silently
            self.saved = {o: o.matrix_world.copy() for o in self.targets}
            if not self.begin(context, event):
                self._restore()
                return {'CANCELLED'}
            context.window_manager.modal_handler_add(self)
            _running.add(self)
            return {'RUNNING_MODAL'}
        except Exception:
            traceback.print_exc()
            self._restore()
            _running.discard(self)
            return {'CANCELLED'}

    def modal(self, context, event):
        # Any failure cancels just this tool; the editor modal underneath carries on
        try:
            result = self._modal(context, event)
        except Exception:
            traceback.print_exc()
            self._restore()
            result = {'CANCELLED'}
        if result & ENDED:
            _running.discard(self)
        return result

    def _modal(self, context, event):
        if event.type in NAV_EVENTS:
            return {'PASS_THROUGH'}
        if event.type == 'MOUSEMOVE':
            self.update(context, event)
            return {'RUNNING_MODAL'}
        if event.value == 'PRESS':
            if event.type in CONFIRM:
                self.finish(context, event)
                context.area.tag_redraw()
                return {'FINISHED'}
            if event.type in ABORT:
                self._restore()
                context.area.tag_redraw()
                return {'CANCELLED'}
            self.keyPress(context, event)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._restore()
        _running.discard(self)

    def _restore(self):
        for obj, matrix in getattr(self, "saved", {}).items():
            try:
                obj.matrix_world = matrix
            except ReferenceError:
                pass  # object was deleted mid-tool
