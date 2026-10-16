"""The "LEGO" workspace: find it, create it if missing, and switch to/from it.

Creation order:
1. Already in the file -> use it.
2. A bundled template (lego_workspace.blend next to this file, containing a
   workspace named "LEGO") -> append it. Use this to ship a custom layout.
3. Otherwise duplicate the current workspace, rename it, and give its 3D
   viewports Solid shading with Object colour and World cavity.

Switching is deferred by Blender: after setting window.workspace the new
screen only exists on the next event-loop iteration.
"""
from pathlib import Path

import bpy

NAME = "LEGO"
TEMPLATE = Path(__file__).parent / "lego_workspace.blend"


def _configureShading(workspace):
    for screen in workspace.screens:
        for area in screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type != 'VIEW_3D':
                    continue
                shading = space.shading
                shading.type = 'SOLID'
                shading.color_type = 'OBJECT'
                shading.show_cavity = True
                shading.cavity_type = 'WORLD'


def ensure(context):
    """Return the LEGO workspace, creating it if needed. None on failure."""
    ws = bpy.data.workspaces.get(NAME)
    if ws is not None:
        return ws

    if TEMPLATE.is_file():
        bpy.ops.workspace.append_activate(idname=NAME, filepath=str(TEMPLATE))
        return bpy.data.workspaces.get(NAME)

    before = set(bpy.data.workspaces.keys())
    bpy.ops.workspace.duplicate()
    new = [w for w in bpy.data.workspaces if w.name not in before]
    if not new:
        return None
    ws = new[0]
    ws.name = NAME
    _configureShading(ws)
    return ws


def enter(context):
    """Switch the current window to the LEGO workspace. Returns the previous
    workspace's name (for exit()), or None if the workspace couldn't be made."""
    previous = context.window.workspace.name
    ws = ensure(context)
    if ws is None:
        return None
    context.window.workspace = ws
    return previous


def exit(context, previousName):
    ws = bpy.data.workspaces.get(previousName)
    if ws is not None:
        context.window.workspace = ws


def isActive(context):
    return context.window.workspace.name == NAME
