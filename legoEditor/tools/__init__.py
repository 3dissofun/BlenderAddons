"""Registry of editor tools.

To add a tool: create tools/<name>.py with a register()/unregister() pair and a
LEGO_OT_<name> operator, import it here, add it to _modules, and bind a key in
KEYMAP. The editor's modal loop calls dispatch(key) for key presses over the
main viewport region.

Keys that only make sense while a tool is running (e.g. W/A/S/D rotating a brick
during a move) are handled by that tool's keyPress(), not registered here.
"""
import traceback

import bpy

from . import move
from .base import isBusy, reset  # noqa: F401  (re-exported for the editor modal)

_modules = (move,)

# Key -> (operator idname, operator properties)
KEYMAP = {
    "G": ("lego.move", {}),
}

# W/A/S/D step-rotate outside a move. Inside one, the move tool's keyPress()
# handles the same keys, so both paths share move.ROTATE_KEYS.
KEYMAP.update({
    key: ("lego.rotate_step", {"axis": axis, "direction": direction})
    for key, (axis, direction) in move.ROTATE_KEYS.items()
})


def dispatch(key):
    """Run the operator bound to a key. Never raises."""
    idname, props = KEYMAP[key]
    try:
        category, name = idname.split(".")
        op = getattr(getattr(bpy.ops, category), name)
        # poll() returns False instead of raising, so a failed poll is a silent skip
        if not op.poll():
            print(f"LEGO: '{idname}' skipped, poll failed (wrong context?)")
            return {'CANCELLED'}
        return op('INVOKE_DEFAULT', **props)
    except Exception:
        traceback.print_exc()
        return {'CANCELLED'}


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
