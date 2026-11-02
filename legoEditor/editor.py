"""
"""
import bpy

import traceback

from .tools import select

panelName = "LEGO"

def inRegion(rectOwner, event):
    return (rectOwner.x <= event.mouse_x < rectOwner.x + rectOwner.width and rectOwner.y <= event.mouse_y < rectOwner.y + rectOwner.height)

class LEGO_OT_EditorToggle(bpy.types.Operator):
    bl_idname = "lego.editor_toggle"
    bl_label = "Toggle LEGO Editor"

    active = False
    stopRequested = False

    def invoke(self, context, event):
        cls = type(self)
        if cls.active: # already running -> act as a toggle off
            cls._stopRequested = True
            return {"FINISHED"}
        if context.area is None or context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "Run from a 3D Viewport")
            return {"CANCELLED"}

        cls.active = True
        cls.stopRequested = False
        self.swallowClick = False # eat the rest of a click we already handled

        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        cls = type(self)
        if cls.stopRequested:
            return {"FINISHED"}

        if event.type == "ESC" and event.value == "PRESS":
            return {"FINISHED"}

        if event.type.startswith("TIMER") or event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            return {"PASS_THROUGH"}

        if event.type == "LEFTMOUSE" and event.value not in {"PRESS", "DOUBLE_CLICK"}:
            if self.swallowClick:
                return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

def register():
    bpy.utils.register_class(LEGO_OT_EditorToggle)

def unregister():
    LEGO_OT_EditorToggle.stopRequested = True
    bpy.utils.unregister_class(LEGO_OT_EditorToggle)
