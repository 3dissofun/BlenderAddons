import bpy, gpu
from gpu_extras.batch import batch_for_shader

import numpy as np
from mathutils import Matrix

from ..operators import common
from .. import utils

handle = None # draw handler

def makeShader():
    # should be called one time to return a shader for the brick selection
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant('MAT4', 'viewProj')
    info.push_constant('MAT4', 'model')
    info.push_constant('VEC4', 'color')
    info.push_constant('FLOAT', 'depthBias')
    info.vertex_in(0, 'VEC3', 'pos')
    info.fragment_out(0, 'VEC4', 'fragColor')
    info.vertex_source(
        "void main() {"
        "  gl_Position = viewProj * model * vec4(pos, 1.0);"
        "  gl_Position.z -= depthBias * gl_Position.w;"
        "}")
    info.fragment_source("void main() { fragColor = color; }")
    return gpu.shader.create_from_info(info)
shader = makeShader()

triCache = {} # mesh name -> tris batch
def getMeshTris(mesh):
    # function to return tris for a mesh, checks cache first, if not, builds them and caches
    tris = triCache.get(mesh.name)
    if tris is None:
        mesh.calc_loop_triangles()
        co = np.empty(len(mesh.vertices) * 3, np.float32)
        mesh.vertices.foreach_get("co", co)
        tris = np.empty(len(mesh.loop_triangles) * 3, np.int32)
        mesh.loop_triangles.foreach_get("vertices", tris)
        tris = batch_for_shader(shader, 'TRIS', {"pos": co.reshape(-1, 3)},
                                indices=tris.reshape(-1, 3))
        triCache[mesh.name] = tris
    return tris

def drawSelected():
    context = bpy.context
    if not common.pollLegoEdit(context):
        return
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(False)
    shader.bind()
    shader.uniform_float("viewProj", context.region_data.perspective_matrix)
    shader.uniform_float("color",(1.0,0.55,0.1,0.25))
    shader.uniform_float("depthBias",5e-5)
    for obj in context.objects_in_mode:
        if not common.isLegoPointCloud(obj) or len(obj.data.points) == 0:
            continue
        pc = obj.data
        selection = common.readSelection(pc)
        if not selection.any():
            continue
        coll = obj.lego_prototypes
        if coll is None:
            continue
        protos = {common.typeFromName(o.name): o for o in coll.all_objects if o.type == 'MESH'}
        types = common.readTypes(pc)[selection]
        worlds = common.instanceWorlds(np.array(obj.matrix_world),
                                       common.readPositions(pc)[selection],
                                       common.readRotations(pc)[selection])
        for t, w in zip(types, worlds):
            proto = protos.get(int(t))
            if proto is None:
                continue
            tris = getMeshTris(proto.data)
            shader.uniform_float("model",Matrix(w.tolist()) @ proto.matrix_world)
            tris.draw(shader)
    gpu.state.color_mask_set(False, False, False, False)
    gpu.state.depth_mask_set(True)
    gpu.state.blend_set('NONE')

def register():
    global handle
    handle = bpy.types.SpaceView3D.draw_handler_add(drawSelected, (), 'WINDOW', 'POST_VIEW')

def unregister():
    global handle
    if handle:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
        handle = None
    triCache.clear()
