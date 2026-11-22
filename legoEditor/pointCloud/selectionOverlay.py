# legoEditor/pointCloud/selectionOverlay.py
import bpy, gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix
from ..operators import common
from .. import utils

_handle = None
_batches = {}   # mesh name -> (tris batch, edges batch)
_shader = gpu.shader.from_builtin('UNIFORM_COLOR')
FILL = (1.0, 0.55, 0.1, 0.25)
WIRE = (1.0, 0.55, 0.1, 1.0)

def _meshBatches(mesh):
    b = _batches.get(mesh.name)
    if b is None:
        mesh.calc_loop_triangles()
        co = np.empty(len(mesh.vertices) * 3, np.float32)
        mesh.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
        tris = np.empty(len(mesh.loop_triangles) * 3, np.int32)
        mesh.loop_triangles.foreach_get("vertices", tris)
        edges = np.empty(len(mesh.edges) * 2, np.int32)
        mesh.edges.foreach_get("vertices", edges)
        b = (batch_for_shader(_shader, 'TRIS', {"pos": co}, indices=tris.reshape(-1, 3)),
             batch_for_shader(_shader, 'LINES', {"pos": co}, indices=edges.reshape(-1, 2)))
        _batches[mesh.name] = b
    return b

def _protoColl(obj):
    return utils.getModifierInput(obj, "LegoInstancer", "Collection")

def _draw():
    ctx = bpy.context
    if ctx.mode != 'EDIT_POINTCLOUD':
        return
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(False)
    gpu.state.line_width_set(2.0)
    _shader.bind()
    for obj in ctx.objects_in_mode:
        if not common.isLegoPointCloud(obj) or len(obj.data.points) == 0:
            continue
        pc = obj.data
        sel = common.readSelection(pc)
        if not sel.any():
            continue
        coll = _protoColl(obj)
        if coll is None:
            continue
        protos = {common.typeFromName(o.name): o for o in coll.all_objects if o.type == 'MESH'}
        types = common.readTypes(pc)[sel]
        worlds = common.instanceWorlds(np.array(obj.matrix_world),
                                       common.readPositions(pc)[sel],
                                       common.readRotations(pc)[sel])
        for t, w in zip(types, worlds):
            proto = protos.get(int(t))
            if proto is None:
                continue
            tris, lines = _meshBatches(proto.data)
            with gpu.matrix.push_pop():
                gpu.matrix.multiply_matrix(Matrix(w.tolist()) @ proto.matrix_world)
                _shader.uniform_float("color", FILL); tris.draw(_shader)
                _shader.uniform_float("color", WIRE); lines.draw(_shader)
    gpu.state.depth_mask_set(True)
    gpu.state.blend_set('NONE')

def register():
    global _handle
    _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')

def unregister():
    global _handle
    if _handle:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    _batches.clear()
