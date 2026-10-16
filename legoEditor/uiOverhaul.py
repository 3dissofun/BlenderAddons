import traceback

import bpy

from . import dimming, tools, workspace
from .tools.base import NAV_EVENTS

panelName = "LEGO"

# Keys over the main viewport that Blender should still handle itself
PASS_KEYS = {
    "N",          # toggle the sidebar (N panel)
    "LEFTMOUSE",  # click / shift-click / box select, via Blender's own selection
}


def _hit(rectOwner, event):
    return (rectOwner.x <= event.mouse_x < rectOwner.x + rectOwner.width and
            rectOwner.y <= event.mouse_y < rectOwner.y + rectOwner.height)


class LEGO_OT_editorToggle(bpy.types.Operator):
    bl_idname = "lego.editor_toggle"
    bl_label = "Toggle LEGO Editor Environment"

    _active = False  # class-level flag so the panel and a second call can see it
    _stop_requested = False

    # ---------- lifecycle ----------
    def invoke(self, context, event):
        cls = type(self)
        if cls._active:  # already running -> act as a toggle off
            cls._stop_requested = True
            return {"FINISHED"}
        if context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "Run from a 3D Viewport")
            return {"CANCELLED"}

        self.previousWorkspace = workspace.enter(context)
        if self.previousWorkspace is None:
            self.report({"ERROR"}, "Could not create the LEGO workspace")
            return {"CANCELLED"}

        cls._active = True
        cls._stop_requested = False

        # The workspace switch only lands on the next event-loop pass, so the
        # LEGO viewport doesn't exist yet. The modal finishes setup (see
        # _tryActivate) once it does; the timer guarantees it gets events.
        self.ready = False
        self.areaPtr = None
        self.handles = []
        self.timer = context.window_manager.event_timer_add(0.05, window=context.window)

        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _tryActivate(self, context):
        # Finish setup once the LEGO workspace is showing. Returns True when ready.
        if not workspace.isActive(context):
            return False
        areas = [a for a in context.window.screen.areas if a.type == "VIEW_3D"]
        if not areas:
            self.report({"ERROR"}, "LEGO workspace has no 3D viewport")
            type(self)._stop_requested = True
            return False
        main = max(areas, key=lambda a: a.width * a.height)
        self.areaPtr = main.as_pointer()
        main.spaces.active.show_region_ui = True  # open the N panel on entry
        self.handles = dimming.install(self.areaPtr)
        self.ready = True
        self._redrawAll(context)
        return True

    def _cleanup(self, context):
        # Every step is guarded on its own so one failure can't skip the rest
        # (a half-run cleanup is what leaves the UI dimmed). Safe to call twice.
        def attempt(fn):
            try:
                fn()
            except Exception:
                traceback.print_exc()

        def removeTimer():
            if self.timer is not None:
                context.window_manager.event_timer_remove(self.timer)
                self.timer = None

        def leaveWorkspace():
            # Go back to where the user came from (unless they already left LEGO)
            if workspace.isActive(context):
                workspace.exit(context, self.previousWorkspace)

        attempt(removeTimer)
        attempt(tools.reset)
        attempt(lambda: dimming.remove(self.handles))
        attempt(leaveWorkspace)
        type(self)._active = False
        attempt(lambda: self._redrawAll(context))

    @staticmethod
    def _redrawAll(context):
        for win in context.window_manager.windows:
            for a in win.screen.areas:
                a.tag_redraw()

    def cancel(self, context):  # called if Blender closes the modal (file load etc.)
        self._cleanup(context)

    # ---------- event filtering ----------
    def modal(self, context, event):
        # If anything here fails, tear the environment down instead of leaving
        # the UI dimmed with a dead modal underneath it.
        try:
            return self._modal(context, event)
        except Exception:
            traceback.print_exc()
            self._cleanup(context)
            return {"CANCELLED"}

    def _modal(self, context, event):
        cls = type(self)
        if cls._stop_requested:
            self._cleanup(context)
            return {"FINISHED"}

        # Wait for the workspace switch to land
        if not self.ready:
            self._tryActivate(context)
            return {"PASS_THROUGH"}

        # A running tool owns the input: pass everything through so its own keys
        # (W/A/S/D, ESC, confirm) reach it instead of being swallowed here.
        if tools.isBusy():
            return {"PASS_THROUGH"}

        if event.type == "ESC" and event.value == "PRESS":
            self._cleanup(context)
            return {"FINISHED"}

        # The user changed workspace some other way: leave the editor
        if not workspace.isActive(context):
            self._cleanup(context)
            return {"FINISHED"}

        # Never block housekeeping events
        if event.type.startswith("TIMER") or event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            return {"PASS_THROUGH"}

        # Which area is the mouse over?
        area = next((a for a in context.window.screen.areas if _hit(a, event)), None)
        if area is None or area.as_pointer() != self.areaPtr:
            return {"RUNNING_MODAL"}  # swallow everything over disabled areas

        # Inside our area: find the region (check non-WINDOW first, they overlap it)
        regions = sorted(area.regions, key=lambda r: r.type == "WINDOW")
        region = next((r for r in regions if _hit(r, event)), None)
        if region is not None and region.type != "WINDOW":
            return {"PASS_THROUGH"}  # header / sidebar / toolbar stay usable

        # Main viewport region: navigation passes through, tools are dispatched, rest is blocked
        if event.type in NAV_EVENTS or event.type in PASS_KEYS:
            return {"PASS_THROUGH"}

        if event.value == "PRESS" and event.type in tools.KEYMAP:
            # This modal was started in the pre-switch workspace, so its own
            # context.area/region can be stale or None. Run the tool against the
            # LEGO viewport explicitly, otherwise every tool's poll() fails.
            viewRegion = next((r for r in area.regions if r.type == "WINDOW"), None)
            if viewRegion is not None:
                with context.temp_override(window=context.window, screen=context.window.screen,
                                           area=area, region=viewRegion):
                    tools.dispatch(event.type)  # a modal tool now sits on top of this one
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}


class LEGO_PT_panel(bpy.types.Panel):
    bl_label = "LEGO Editor"
    bl_idname = "MYMODE_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = panelName

    def draw(self, context):
        active = LEGO_OT_editorToggle._active
        wm = context.window_manager
        layout = self.layout
        layout.operator("lego.editor_toggle",
                        text="Exit LEGO Editor" if active else "Enter LEGO Editor",
                        depress=active)
        layout.prop(wm, "brick_id")
        layout.operator("lego.import_brick")
        layout.operator("lego.import_random")

        layout.prop(wm, "lego_show_snaps", toggle=True)

        if active:
            layout.label(text="magic is real")


classes = (LEGO_OT_editorToggle, LEGO_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    tools.register()


def unregister():
    LEGO_OT_editorToggle._stop_requested = True
    tools.unregister()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
