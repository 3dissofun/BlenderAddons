"""Dim every area except one, while the LEGO editor is active."""
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

# Every space/region combo we want to dim. Invalid pairs are skipped on install.
SPACE_NAMES = [
    "SpaceView3D", "SpaceProperties", "SpaceOutliner", "SpaceTextEditor",
    "SpaceNodeEditor", "SpaceImageEditor", "SpaceSequenceEditor",
    "SpaceDopeSheetEditor", "SpaceGraphEditor", "SpaceFileBrowser",
    "SpaceConsole", "SpaceInfo", "SpaceClipEditor", "SpaceSpreadsheet",
]
REGION_TYPES = ["WINDOW", "HEADER", "UI", "TOOLS", "FOOTER", "TOOL_HEADER",
                "NAVIGATION_BAR", "HUD", "CHANNELS", "TOOL_PROPS"]

DIM_COLOUR = (0.0, 0.0, 0.0, 0.5)


def _drawDim(allowedAreaPtr):
    """POST_PIXEL callback: darken everything except the allowed area."""
    area, region = bpy.context.area, bpy.context.region
    if area is None or region is None:
        return
    if area.as_pointer() == allowedAreaPtr:
        return
    w, h = region.width, region.height
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRI_FAN",
                             {"pos": ((0, 0), (w, 0), (w, h), (0, h))})
    gpu.state.blend_set("ALPHA")
    shader.uniform_float("color", DIM_COLOUR)
    batch.draw(shader)
    gpu.state.blend_set("NONE")


def install(allowedAreaPtr):
    """Add the dimming draw handlers. Returns a list to pass to remove()."""
    handles = []
    for name in SPACE_NAMES:
        spaceCls = getattr(bpy.types, name, None)
        if spaceCls is None:
            continue
        for rt in REGION_TYPES:
            try:
                h = spaceCls.draw_handler_add(_drawDim, (allowedAreaPtr,), rt, "POST_PIXEL")
                handles.append((spaceCls, h, rt))
            except (ValueError, TypeError):
                pass  # that region type doesn't exist for this space
    return handles


def remove(handles):
    for spaceCls, h, rt in handles:
        spaceCls.draw_handler_remove(h, rt)
    handles.clear()
