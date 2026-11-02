"""
LEGO point editor.

Same environment as the main LEGO editor (workspace switch, dimmed UI, N-panel), but the
only thing it does is let you select and move point-cloud points:

    LMB              select the brick under the mouse (Shift: toggle / extend)
    G                move the selected points with the mouse
        X / Y / Z    while moving: lock to that world axis (press again to unlock)
        LMB / Enter  confirm the move
        RMB / Esc    cancel the move (points go back where they were)
    N                toggle the sidebar
    Esc              leave the editor (when not moving)

Points are moved in world space, then converted back to point-cloud units, undoing
the object's transform and the LegoInstancer ScaleFactor, so the brick follows
the mouse exactly.
"""
import traceback

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

from . import dimming, utils, workspace
from .tools.base import NAV_EVENTS
from .tools import select

panelName = "LEGO"

# Keys over the main viewport that Blender should still handle itself
PASS_KEYS = {"N"}

GRAB_KEY = "G"
CONFIRM_KEYS = {"RET", "NUMPAD_ENTER"}
CANCEL_KEYS = {"RIGHTMOUSE", "ESC"}
AXIS_KEYS = {"X": 0, "Y": 1, "Z": 2}

INSTANCER_MODIFIER = "LegoInstancer"
SCALE_INPUT = "ScaleFactor"


# ---------------------------------------------------------------- point helpers
def _hit(rectOwner, event):
    return (rectOwner.x <= event.mouse_x < rectOwner.x + rectOwner.width and
            rectOwner.y <= event.mouse_y < rectOwner.y + rectOwner.height)


def selectedPointIndices(pcObj):
    """Indices of the points selected in Edit Mode."""
    pc = pcObj.data
    attr = pc.attributes.get(".selection")
    if attr is None:
        return []
    values = [False if attr.data_type == 'BOOLEAN' else 0.0] * len(pc.points)
    attr.data.foreach_get("value", values)
    return [i for i, v in enumerate(values) if v]


def readPositions(pc):
    flat = [0.0] * (len(pc.points) * 3)
    pc.attributes["position"].data.foreach_get("vector", flat)
    return flat


def writePositions(pc, flat):
    pc.attributes["position"].data.foreach_set("vector", flat)
    pc.update_tag()


def legoScale(pcObj):
    """The instancer's ScaleFactor as a per-axis Vector (works for float or vector inputs)."""
    s = utils.getModifierInput(pcObj, INSTANCER_MODIFIER, SCALE_INPUT)
    if s is None:
        return Vector((1.0, 1.0, 1.0))
    if isinstance(s, (int, float)):
        s = (s, s, s)
    return Vector([c if c else 1.0 for c in s])   # treat 0 as 1 to avoid dividing by zero


class PointTarget:
    """The selected points of one point cloud, moved together by a world-space offset."""

    def __init__(self, pcObj, indices):
        self.obj = pcObj
        self.indices = indices
        self.scale = legoScale(pcObj)
        self.original = readPositions(pcObj.data)
        # Brick world position = matrix_world @ (point * scale), so a world offset is
        # turned back into point units through the inverse of both.
        self.worldToLocal = pcObj.matrix_world.inverted_safe().to_3x3()

    def worldPosition(self, index):
        """Where the brick for this point appears in the world."""
        p = self.original[index * 3:index * 3 + 3]
        scaled = Vector((p[0] * self.scale.x, p[1] * self.scale.y, p[2] * self.scale.z))
        return self.obj.matrix_world @ scaled

    def apply(self, worldDelta):
        # Always start from the original positions, so moves never accumulate errors.
        d = self.worldToLocal @ worldDelta
        d = Vector((d.x / self.scale.x, d.y / self.scale.y, d.z / self.scale.z))
        flat = list(self.original)
        for i in self.indices:
            flat[i * 3] += d.x
            flat[i * 3 + 1] += d.y
            flat[i * 3 + 2] += d.z
        writePositions(self.obj.data, flat)

    def restore(self):
        writePositions(self.obj.data, self.original)


def pointTargets(context):
    """One PointTarget per point cloud in Edit Mode that has selected points."""
    targets = []
    for obj in context.objects_in_mode:
        if obj.type == 'POINTCLOUD':
            indices = selectedPointIndices(obj)
            if indices:
                targets.append(PointTarget(obj, indices))
    return targets


# ---------------------------------------------------------------- the editor
class LEGO_OT_pointEditorToggle(bpy.types.Operator):
    bl_idname = "lego.point_editor_toggle"
    bl_label = "Toggle LEGO Point Editor"

    _active = False  # class-level flag so the panel and a second call can see it
    _stop_requested = False

    # ---------- lifecycle ----------
    def invoke(self, context, event):
        cls = type(self)
        if cls._active:  # already running -> act as a toggle off
            cls._stop_requested = True
            return {"FINISHED"}
        if context.area is None or context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "Run from a 3D Viewport")
            return {"CANCELLED"}

        self.previousWorkspace = workspace.enter(context)
        if self.previousWorkspace is None:
            self.report({"ERROR"}, "Could not create the LEGO workspace")
            return {"CANCELLED"}

        cls._active = True
        cls._stop_requested = False

        # The workspace switch only lands on the next event-loop pass; the modal
        # finishes setup in _tryActivate once the LEGO viewport exists.
        self.ready = False
        self.areaPtr = None
        self.handles = []
        self.grab = None            # state of an in-progress move, or None
        self.swallowClick = False   # eat the rest of a click we already handled
        self.timer = context.window_manager.event_timer_add(0.05, window=context.window)

        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _tryActivate(self, context):
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
        context.workspace.status_text_set(
            "LMB: select brick   G: move points   X/Y/Z: lock axis   Esc: exit point editor")
        self.ready = True
        self._redrawAll(context)
        return True

    def _cleanup(self, context):
        # Every step is guarded on its own so one failure can't skip the rest.
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
            if workspace.isActive(context):
                workspace.exit(context, self.previousWorkspace)

        attempt(lambda: self._cancelGrab(context))   # never leave points half-moved
        attempt(removeTimer)
        attempt(lambda: dimming.remove(self.handles))
        attempt(lambda: context.workspace.status_text_set(None))
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

    # ---------- helpers ----------
    def _viewArea(self, context):
        """The LEGO viewport area and its main (WINDOW) region, looked up fresh each time."""
        area = next((a for a in context.window.screen.areas if a.as_pointer() == self.areaPtr), None)
        if area is None:
            return None, None
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        return area, region

    @staticmethod
    def _regionCoord(event, region):
        return (event.mouse_x - region.x, event.mouse_y - region.y)

    # ---------- moving points ----------
    def _startGrab(self, context, event):
        area, region = self._viewArea(context)
        if region is None:
            return
        targets = pointTargets(context)
        if not targets:
            self.report({"INFO"}, "Select some bricks first")
            return

        # Pivot = average world position of every selected brick
        positions = [t.worldPosition(i) for t in targets for i in t.indices]
        pivot = sum(positions, Vector()) / len(positions)

        # Mouse positions are projected onto the plane through the pivot facing the view,
        # so the bricks stay at the same depth and follow the cursor.
        start = view3d_utils.region_2d_to_location_3d(
            region, region.data, self._regionCoord(event, region), pivot)

        self.grab = {
            "targets": targets,
            "pivot": pivot,
            "offset": pivot - start,
            "axis": None,
        }
        self._updateGrab(context, event)

    def _updateGrab(self, context, event):
        g = self.grab
        area, region = self._viewArea(context)
        if region is None:
            return
        loc = view3d_utils.region_2d_to_location_3d(
            region, region.data, self._regionCoord(event, region), g["pivot"])
        delta = loc + g["offset"] - g["pivot"]

        if g["axis"] is not None:
            locked = Vector()
            locked[g["axis"]] = delta[g["axis"]]
            delta = locked

        for t in g["targets"]:
            t.apply(delta)

        axisName = "free" if g["axis"] is None else "XYZ"[g["axis"]]
        area.header_text_set(f"Move points ({axisName}):  "
                             f"{delta.x:.3f}, {delta.y:.3f}, {delta.z:.3f}   "
                             f"LMB/Enter confirm, RMB/Esc cancel")
        area.tag_redraw()

    def _endGrab(self, context):
        self.grab = None
        area, _region = self._viewArea(context)
        if area is not None:
            area.header_text_set(None)
            area.tag_redraw()

    def _confirmGrab(self, context):
        self._endGrab(context)
        try:
            bpy.ops.ed.undo_push(message="Move points")
        except Exception:
            traceback.print_exc()

    def _cancelGrab(self, context):
        if self.grab is None:
            return
        for t in self.grab["targets"]:
            t.restore()
        self._endGrab(context)

    def _grabModal(self, context, event):
        """While moving, this modal owns the input."""
        if event.type.startswith("TIMER"):
            return {"PASS_THROUGH"}

        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            self._updateGrab(context, event)
            return {"RUNNING_MODAL"}

        if event.value == "PRESS":
            if event.type in AXIS_KEYS:
                axis = AXIS_KEYS[event.type]
                self.grab["axis"] = None if self.grab["axis"] == axis else axis
                self._updateGrab(context, event)
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" or event.type in CONFIRM_KEYS:
                self._confirmGrab(context)
                self.swallowClick = event.type == "LEFTMOUSE"  # eat its release
                return {"RUNNING_MODAL"}
            if event.type in CANCEL_KEYS:
                self._cancelGrab(context)
                return {"RUNNING_MODAL"}

        # Let the user zoom / orbit while moving
        if event.type in NAV_EVENTS:
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"}

    # ---------- event filtering ----------
    def modal(self, context, event):
        # If anything fails, tear the environment down instead of leaving the UI dimmed.
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

        if not self.ready:
            self._tryActivate(context)
            return {"PASS_THROUGH"}

        # A move in progress owns the input (its own Esc cancels the move, not the editor)
        if self.grab is not None:
            return self._grabModal(context, event)

        if event.type == "ESC" and event.value == "PRESS":
            self._cleanup(context)
            return {"FINISHED"}

        if not workspace.isActive(context):
            self._cleanup(context)
            return {"FINISHED"}

        if event.type.startswith("TIMER") or event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            return {"PASS_THROUGH"}

        # After we handled a press, eat the rest of that click (release / click / drag)
        # so Blender's own select doesn't run on release and undo our selection.
        if event.type == "LEFTMOUSE" and event.value not in {"PRESS", "DOUBLE_CLICK"}:
            if self.swallowClick:
                return {"RUNNING_MODAL"}

        # Which area is the mouse over?
        area = next((a for a in context.window.screen.areas if _hit(a, event)), None)
        if area is None or area.as_pointer() != self.areaPtr:
            return {"RUNNING_MODAL"}  # swallow everything over disabled areas

        # Inside our area: find the region (check non-WINDOW first, they overlap it)
        regions = sorted(area.regions, key=lambda r: r.type == "WINDOW")
        region = next((r for r in regions if _hit(r, event)), None)
        if region is not None and region.type != "WINDOW":
            return {"PASS_THROUGH"}  # header / sidebar / toolbar stay usable

        viewRegion = region if region is not None else next(
            (r for r in area.regions if r.type == "WINDOW"), None)

        # Click: select the brick under the mouse
        if event.type == "LEFTMOUSE" and event.value in {"PRESS", "DOUBLE_CLICK"}:
            self.swallowClick = False
            if viewRegion is not None:
                with context.temp_override(window=context.window, screen=context.window.screen,
                                           area=area, region=viewRegion):
                    if select.raycastAndSelect(context, event, self._regionCoord(event, viewRegion)):
                        self.swallowClick = True
                        return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}  # missed: let Blender select / box-select

        # G: move the selected points
        if event.type == GRAB_KEY and event.value == "PRESS":
            self._startGrab(context, event)
            return {"RUNNING_MODAL"}

        if event.type in NAV_EVENTS or event.type in PASS_KEYS:
            return {"PASS_THROUGH"}

        return {"RUNNING_MODAL"}


class LEGO_PT_pointEditorPanel(bpy.types.Panel):
    bl_label = "LEGO Point Editor"
    bl_idname = "LEGO_PT_point_editor"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = panelName

    def draw(self, context):
        active = LEGO_OT_pointEditorToggle._active
        wm = context.window_manager
        layout = self.layout
        layout.operator("lego.point_editor_toggle",
                        text="Exit Point Editor" if active else "Enter Point Editor",
                        depress=active)
        layout.prop(wm, "brick_id")
        layout.operator("lego.import_brick")
        layout.operator("lego.import_random")

        layout.prop(wm, "lego_show_snaps", toggle=True)

        if active:
            layout.label(text="LMB select, G move, X/Y/Z lock axis")


classes = (LEGO_OT_pointEditorToggle, LEGO_PT_pointEditorPanel)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    LEGO_OT_pointEditorToggle._stop_requested = True
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
