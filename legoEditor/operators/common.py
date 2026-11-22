"""
Code shared by the lego point cloud operators (select.py, move.py, rotate.py):
the poll rules, point attribute access, brick transform maths, the per-cloud state used
while moving or rotating bricks, and keymap registration.

Point positions are stored in LDU in the cloud's local space, and the object's matrix (with its
scale) places them:
    brick world matrix = object matrix @ T(position) @ R(rotation) @ prototype matrix
"""
import bpy
import numpy as np
from mathutils import Vector

from .. import utils

# Lego clouds \/

def isLegoPointCloud(obj):
    if obj is None:
        return False
    return obj.type == "POINTCLOUD" and getattr(obj, "is_lego_model", False)

def pollLegoEdit(context):
    # Only take over in point cloud edit mode, in the 3D viewport, when at least one lego
    # cloud is being edited. Returning False lets the keymap fall through to Blender's own.
    if context.mode != 'EDIT_POINTCLOUD':
        return False
    if context.area is None or context.area.type != 'VIEW_3D':
        return False
    return any(isLegoPointCloud(o) for o in context.objects_in_mode)

def typeFromName(name):
    # prototypes are named with a 5 digit type prefix, e.g. "00005"
    try:
        return int(name[:5])
    except ValueError:
        return None

# Point cloud attributes \/

def readPositions(pc):
    n = len(pc.points)
    flat = np.empty(n * 3, dtype=np.float32)
    pc.attributes["position"].data.foreach_get("vector", flat)
    return flat.reshape(n, 3)

def writePositions(pc, positions):
    pc.attributes["position"].data.foreach_set("vector", positions.astype(np.float32).ravel())
    pc.update_tag()

def readRotations(pc):
    # (n, 4) w x y z; points without a rotation attribute are unrotated
    n = len(pc.points)
    attr = pc.attributes.get("rotation")
    if attr is None:
        out = np.zeros((n, 4), dtype=np.float32)
        out[:, 0] = 1.0
        return out
    flat = np.empty(n * 4, dtype=np.float32)
    attr.data.foreach_get("value", flat)
    return flat.reshape(n, 4)

def writeRotations(pc, quats):
    attr = pc.attributes.get("rotation")
    if attr is None:
        attr = pc.attributes.new("rotation", 'QUATERNION', 'POINT')
    attr.data.foreach_set("value", quats.astype(np.float32).ravel())
    pc.update_tag()

def readTypes(pc):
    n = len(pc.points)
    attr = pc.attributes.get("type")
    vals = np.zeros(n, dtype=np.int32)
    if attr is not None:
        attr.data.foreach_get("value", vals)
    return vals

def readSelection(pc):
    # Blender treats a missing .selection attribute as "everything selected"
    n = len(pc.points)
    attr = pc.attributes.get(".selection")
    if attr is None:
        return np.ones(n, dtype=bool)
    if attr.data_type == 'BOOLEAN':
        vals = np.zeros(n, dtype=bool)
        attr.data.foreach_get("value", vals)
        return vals
    vals = np.zeros(n, dtype=np.float32)
    attr.data.foreach_get("value", vals)
    return vals > 0.5

# Maths \/

def quatsToMatrices(quats):
    # (n, 4) w x y z -> (n, 3, 3)
    q = np.asarray(quats, dtype=np.float64)
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    w, x, y, z = q.T
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
        np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
        np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1),
    ], 1)

def instanceWorlds(objMatrix, positions, quats):
    # world matrix of each instanced brick, before its prototype's own matrix (see top of file)
    n = len(positions)
    local = np.zeros((n, 4, 4))
    local[:, :3, :3] = quatsToMatrices(quats)
    local[:, :3, 3] = np.asarray(positions, dtype=np.float64)
    local[:, 3, 3] = 1.0
    return objMatrix @ local

def snapToGrid(values, step):
    return np.round(np.asarray(values, dtype=np.float64) / step) * step

# Clouds being moved or rotated \/

class CloudMove:
    # The state of one point cloud whose selected bricks are being moved or rotated: its
    # original positions and rotations, which points move, and how to turn a world space move
    # into an LDU move in its local space
    def __init__(self, obj):
        self.obj = obj
        self.pc = obj.data
        self.orig = readPositions(self.pc)
        self.origRot = readRotations(self.pc)
        self.mask = readSelection(self.pc)
        self.matrix = np.array(obj.matrix_world, dtype=np.float64)
        # a direction, so no translation: undo the object's rotation and scale
        self.worldToLdu = obj.matrix_world.inverted_safe().to_3x3()
        self.anchor = self.orig[self.mask][0].astype(np.float64) if self.mask.any() else None
        self.tilted = False  # rotations currently differ from origRot

    def worldPoints(self):
        # world space positions of the selected points, as drawn by the instancer
        mat = self.obj.matrix_world
        return [mat @ Vector(p) for p in self.orig[self.mask]]

    def lduDelta(self, worldDelta, step=None, absolute=False):
        delta = np.array(self.worldToLdu @ worldDelta, dtype=np.float64)
        if step is None:
            return delta
        if absolute:
            # put the first selected brick on the grid, the rest keep their offsets to it
            return snapToGrid(self.anchor + delta, step) - self.anchor
        # snap the distance moved, so bricks already off grid stay as they were relative to it
        return snapToGrid(delta, step)

    def apply(self, lduDelta):
        # translate the selected bricks by an LDU move, keeping their original rotations
        positions = self.orig.copy()
        positions[self.mask] += lduDelta.astype(np.float32)
        writePositions(self.pc, positions)
        if self.tilted:  # coming off a transform that turned the bricks
            writeRotations(self.pc, self.origRot)
            self.tilted = False

    def applyTransform(self, transform):
        # transform: world 4x4 to left-multiply onto each selected brick's world matrix
        # (a brick snap, or a rotation about a pivot).
        # In the cloud's local space that's A = M^-1 @ transform @ M, applied to T(p) @ R
        A = np.linalg.inv(self.matrix) @ np.asarray(transform, dtype=np.float64) @ self.matrix
        rot, move = A[:3, :3], A[:3, 3]
        sel = self.mask

        positions = self.orig.astype(np.float64)
        positions[sel] = positions[sel] @ rot.T + move
        writePositions(self.pc, positions)

        quats = self.origRot.copy()
        quats[sel] = utils.matricesToQuaternions(rot @ quatsToMatrices(self.origRot[sel]))
        writeRotations(self.pc, quats)
        self.tilted = True

    def restore(self):
        writePositions(self.pc, self.orig)
        writeRotations(self.pc, self.origRot)


def gatherClouds(context):
    # a CloudMove for each lego cloud being edited that has something selected
    clouds = [CloudMove(o) for o in context.objects_in_mode
              if isLegoPointCloud(o) and len(o.data.points) > 0]
    return [c for c in clouds if c.mask.any()]

def selectionCentre(clouds):
    # world space middle of every selected brick
    points = [p for c in clouds for p in c.worldPoints()]
    return sum(points, Vector()) / len(points)

# Modal helpers \/

def clearHeader(context):
    context.area.header_text_set(None)
    context.area.tag_redraw()

# Keymaps \/

class Keymaps:
    # The keymap items one operator file adds to the point cloud edit mode keymap.
    #   keymaps = Keymaps()
    #   keymaps.add("lego.move_instance", 'G', 'PRESS')    in register
    #   keymaps.clear()                                     in unregister
    def __init__(self):
        self.items = []

    def add(self, idname, key, value, **modifiers):
        # returns the keymap item so properties can be set, or None in background mode
        kc = bpy.context.window_manager.keyconfigs.addon
        if kc is None:  # background mode
            return None
        # point cloud edit mode keymap; mode keymaps run before the generic "3D View" one
        km = kc.keymaps.new(name="Point Cloud", space_type='EMPTY')
        kmi = km.keymap_items.new(idname, key, value, **modifiers)
        self.items.append((km, kmi))
        return kmi

    def clear(self):
        for km, kmi in self.items:
            km.keymap_items.remove(kmi)
        self.items.clear()
