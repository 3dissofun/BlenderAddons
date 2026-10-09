import bpy
import gpu
from gpu_extras.batch import batch_for_shader

panelName = "LEGO"

# Every space/region combo we want to dim. Invalid pairs are skipped on registration.
SPACE_NAMES = [
    "SpaceView3D", "SpaceProperties", "SpaceOutliner", "SpaceTextEditor",
    "SpaceNodeEditor", "SpaceImageEditor", "SpaceSequenceEditor",
    "SpaceDopeSheetEditor", "SpaceGraphEditor", "SpaceFileBrowser",
    "SpaceConsole", "SpaceInfo", "SpaceClipEditor", "SpaceSpreadsheet",
]
REGION_TYPES = ["WINDOW", "HEADER", "UI", "TOOLS", "FOOTER", "TOOL_HEADER",
                "NAVIGATION_BAR", "HUD", "CHANNELS", "TOOL_PROPS"]

NAV_EVENTS = {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE",
              "WHEELOUTMOUSE", "TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE",
              "NDOF_MOTION"}

def _draw_dim(allowed_area_ptr):
    """POST_PIXEL callback: darken everything except the allowed area."""
    area, region = bpy.context.area, bpy.context.region
    if area is None or region is None:
        return
    if area.as_pointer() == allowed_area_ptr:
        return
    w, h = region.width, region.height
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRI_FAN",
                             {"pos": ((0, 0), (w, 0), (w, h), (0, h))})
    gpu.state.blend_set("ALPHA")
    shader.uniform_float("color", (0.0, 0.0, 0.0, 0.5))
    batch.draw(shader)
    gpu.state.blend_set("NONE")


def _hit(rect_owner, event):
    return (rect_owner.x <= event.mouse_x < rect_owner.x + rect_owner.width and
            rect_owner.y <= event.mouse_y < rect_owner.y + rect_owner.height)

class LEGO_OT_editorToggle(bpy.types.Operator):
    bl_idname = "lego.editor_toggle"
    bl_label = "Toggle LEGO Editor Environment"

    _active = False # class-level flag so the panel and a second call can see it
    _stop_requested = False

    # ---------- lifecycle ----------
    def invoke(self, context, event):
        cls = type(self)
        if cls._active:                      # already running -> act as a toggle off
            cls._stop_requested = True
            return {"FINISHED"}
        if context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "Run from a 3D Viewport")
            return {"CANCELLED"}

        cls._active = True
        cls._stop_requested = False
        self.area_ptr = context.area.as_pointer()
        self.handles = []

        # Save UI state we are going to change
        space = context.space_data
        self.saved = {"overlays": space.overlay.show_overlays}
        # e.g. space.show_region_toolbar = False  (save + restore anything you touch)

        # Install dimming handlers
        for name in SPACE_NAMES:
            space_cls = getattr(bpy.types, name, None)
            if space_cls is None:
                continue
            for rt in REGION_TYPES:
                try:
                    h = space_cls.draw_handler_add(_draw_dim, (self.area_ptr,), rt, "POST_PIXEL")
                    self.handles.append((space_cls, h, rt))
                except (ValueError, TypeError):
                    pass  # that region type doesn't exist for this space

        context.window_manager.modal_handler_add(self)
        self._redraw_all(context)
        return {"RUNNING_MODAL"}

    def _cleanup(self, context):
        for space_cls, h, rt in self.handles:
            space_cls.draw_handler_remove(h, rt)
        self.handles.clear()
        # restore saved UI state here
        type(self)._active = False
        self._redraw_all(context)

    @staticmethod
    def _redraw_all(context):
        for win in context.window_manager.windows:
            for a in win.screen.areas:
                a.tag_redraw()

    def cancel(self, context): # called if Blender closes the modal (file load etc.)
        self._cleanup(context)

    # ---------- event filtering ----------
    def modal(self, context, event):
        cls = type(self)
        if cls._stop_requested or (event.type == "ESC" and event.value == "PRESS"):
            self._cleanup(context)
            return {"FINISHED"}

        # Never block housekeeping events
        if event.type.startswith("TIMER") or event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            return {"PASS_THROUGH"}

        # Which area is the mouse over?
        area = next((a for a in context.window.screen.areas if _hit(a, event)), None)
        if area is None or area.as_pointer() != self.area_ptr:
            return {"RUNNING_MODAL"} # swallow everything over disabled areas

        # Inside our area: find the region (check non-WINDOW first, they overlap it)
        regions = sorted(area.regions, key=lambda r: r.type == "WINDOW")
        region = next((r for r in regions if _hit(r, event)), None)
        if region is not None and region.type != "WINDOW":
            return {"PASS_THROUGH"} # header / sidebar / toolbar stay usable

        # Main viewport region: allow navigation, handle our own tools, block the rest
        if event.type in NAV_EVENTS:
            return {"PASS_THROUGH"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            print("my tool: click at", event.mouse_region_x, event.mouse_region_y)
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
        if active:
            layout.label(text="Magic is real")
            layout.prop(wm,"brick_id")
            layout.operator("lego.import_brick")
            layout.operator("lego.import_random")

classes = (LEGO_OT_editorToggle, LEGO_PT_panel)

def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    LEGO_OT_editorToggle._stop_requested = True
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
