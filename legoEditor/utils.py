import bpy

import numpy as np

def getPref(prefName):
    preferences = bpy.context.preferences.addons[__package__].preferences
    return getattr(preferences, prefName, None)

def setModifierInput(modifier, socketName, value):
    # small helper to set modifier input sockets by name
    identifier = next(item.identifier for item in modifier.node_group.interface.items_tree
        if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == socketName)
    getattr(modifier.properties.inputs, identifier).value = value

def getModifierInput(obj, modName, inputName):
    mod = obj.modifiers.get(modName, None)
    if not mod or mod.type != 'NODES' or mod.node_group is None:
        print("LEGO EDITOR, ERROR: Asked to retrieve a value from a modifier that doesnt exist.")
        return None

    for item in mod.node_group.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == inputName:
            inputProp = getattr(mod.properties.inputs, item.identifier, None)
            if inputProp is None:
                return None
            return inputProp.value
    return None

def matricesToQuaternions(matrices):
    # Converts an (N, 4, 4) or (N, 3, 3) array of LDraw matrices into an (N, 4)
    # float32 array of quaternions in Blender's (w, x, y, z) order.
    # Only the rotation part is used; the translation column is ignored.
    rot = np.asarray(matrices, dtype=np.float64)[:, :3, :3]
    if len(rot) == 0:
        return np.empty((0, 4), dtype=np.float32)
 
    # Snap each matrix to the nearest pure rotation. LDraw files often store
    # values with few decimals, so the matrices are slightly off.
    u, _, vt = np.linalg.svd(rot)
    rot = u @ vt
 
    # A mirrored matrix (negative determinant) can't be stored as a quaternion.
    # Use the nearest rotation instead and warn.
    mirrored = np.linalg.det(rot) < 0
    if mirrored.any():
        print(f"WARNING, {int(mirrored.sum())} mirrored matrices can't be stored as "
              f"quaternions; their mirroring is lost")
        u[mirrored, :, 2] *= -1
        rot[mirrored] = u[mirrored] @ vt[mirrored]
 
    r00, r01, r02 = rot[:, 0, 0], rot[:, 0, 1], rot[:, 0, 2]
    r10, r11, r12 = rot[:, 1, 0], rot[:, 1, 1], rot[:, 1, 2]
    r20, r21, r22 = rot[:, 2, 0], rot[:, 2, 1], rot[:, 2, 2]
    trace = r00 + r11 + r22
 
    quats = np.empty((len(rot), 4), dtype=np.float64)
 
    # Pick the most numerically stable formula for each matrix
    # (whichever of the trace or the diagonal values is largest)
    case = np.argmax(np.stack([trace, r00, r11, r22], axis=1), axis=1)
 
    c = case == 0
    s = np.sqrt(trace[c] + 1.0) * 2
    quats[c] = np.stack([0.25 * s,
                         (r21[c] - r12[c]) / s,
                         (r02[c] - r20[c]) / s,
                         (r10[c] - r01[c]) / s], axis=1)
 
    c = case == 1
    s = np.sqrt(1.0 + r00[c] - r11[c] - r22[c]) * 2
    quats[c] = np.stack([(r21[c] - r12[c]) / s,
                         0.25 * s,
                         (r01[c] + r10[c]) / s,
                         (r02[c] + r20[c]) / s], axis=1)
 
    c = case == 2
    s = np.sqrt(1.0 + r11[c] - r00[c] - r22[c]) * 2
    quats[c] = np.stack([(r02[c] - r20[c]) / s,
                         (r01[c] + r10[c]) / s,
                         0.25 * s,
                         (r12[c] + r21[c]) / s], axis=1)
 
    c = case == 3
    s = np.sqrt(1.0 + r22[c] - r00[c] - r11[c]) * 2
    quats[c] = np.stack([(r10[c] - r01[c]) / s,
                         (r02[c] + r20[c]) / s,
                         (r12[c] + r21[c]) / s,
                         0.25 * s], axis=1)
 
    quats /= np.linalg.norm(quats, axis=1, keepdims=True)
    return quats.astype(np.float32)

import cProfile
import pstats
import io

def profileCall(func, *args, statsPath=None, top=30, **kwargs):
    # Runs func(*args, **kwargs) under cProfile and prints the slowest functions.
    # Returns whatever func returns.
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        result = func(*args, **kwargs)
    finally:
        profiler.disable()

    if statsPath:
        profiler.dump_stats(statsPath)  # keep the raw data for a closer look later

    for sortKey in ("tottime", "cumulative"):
        buffer = io.StringIO()
        stats = pstats.Stats(profiler, stream=buffer).strip_dirs().sort_stats(sortKey)
        stats.print_stats(top)
        print(f"\n===== sorted by {sortKey} =====")
        print(buffer.getvalue())
    return result
