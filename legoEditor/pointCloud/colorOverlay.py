"""
Draws lego point cloud bricks in their LDraw colours in Solid mode.
Solid mode can't read per instance attributes, so this draw handler redraws every visible brick
over Blender's own (grey) instances, slightly pulled toward the camera so it wins the depth test.
Faces with code 16 take the brick's "col" point attribute, other faces keep their own "col" face code.

Each cloud is merged into one world space batch (numpy), cached until the depsgraph says the cloud
changed, so a redraw is one draw call per cloud.

Lighting follows the viewport's Solid shading: Flat, Studio (the studio light's solid lights and
ambient, like Workbench's diffuse lighting) and MatCap. Lighting is done in linear and converted to
sRGB, as the overlay buffer is display space. Cavity, shadows, AO and specular are Workbench screen
effects on its own render and can't be applied to an overlay. Nothing is drawn in X-ray.

Register this before the selection overlay so the selection tint draws on top
(the tint must use a larger depth bias than DEPTH_BIAS here).
"""
import bpy, gpu
from gpu_extras.batch import batch_for_shader

import numpy as np
from mathutils import Matrix, Vector

from ..operators import common
from ..operators import color as colorOps
from ..ldraw.col import colors

handle = None # draw handler

DEPTH_BIAS = 2e-5 # smaller than the selection tint's bias
DEFAULT_SRGB = (0.6, 0.6, 0.6) # bricks still on 16 (no colour set)

MODE_FLAT, MODE_STUDIO, MODE_MATCAP = 0.0, 1.0, 2.0

# Shader \/

def makeShader():
    # should be called one time to return the brick shader
    iface = gpu.types.GPUStageInterfaceInfo("lego_col_iface")
    iface.smooth('VEC3', 'vNor')
    iface.smooth('VEC3', 'vCol')

    info = gpu.types.GPUShaderCreateInfo()
    info.typedef_source(
        "struct LightData {"
        "  vec4 dirWrap[4];" # view space direction to the light, wrap (smooth)
        "  vec4 diffuse[4];"
        "  vec4 ambient;"
        "  vec4 mode;"       # x: 0 flat, 1 studio, 2 matcap
        "};")
    info.uniform_buf(0, 'LightData', 'lights')
    info.sampler(0, 'FLOAT_2D', 'matcap')
    info.push_constant('MAT4', 'viewProj')
    info.push_constant('MAT4', 'viewMat')
    info.vertex_in(0, 'VEC3', 'pos') # world space
    info.vertex_in(1, 'VEC3', 'nor') # world space, not normalised
    info.vertex_in(2, 'VEC3', 'col') # linear rgb
    info.vertex_out(iface)
    info.fragment_out(0, 'VEC4', 'fragColor')
    info.vertex_source(
        "void main() {"
        "  gl_Position = viewProj * vec4(pos, 1.0);"
        f"  gl_Position.z -= {DEPTH_BIAS:.8f} * gl_Position.w;"
        "  vNor = mat3(viewMat) * nor;"
        "  vCol = col;"
        "}")
    info.fragment_source(
        "vec3 toSrgb(vec3 c) {"
        "  c = max(c, vec3(0.0));"
        "  return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(vec3(0.0031308), c));"
        "}"
        "void main() {"
        "  vec3 n = normalize(vNor);"
        "  if (n.z < 0.0) n = -n;" # face the camera, LDraw winding can't be trusted
        "  vec3 light = vec3(1.0);"
        "  int mode = int(lights.mode.x + 0.5);"
        "  if (mode == 2) {"
        "    light = texture(matcap, n.xy * 0.5 + 0.5).rgb;"
        "  } else if (mode == 1) {"
        "    light = lights.ambient.rgb;"
        "    for (int i = 0; i < 4; i++) {"
        "      float w = lights.dirWrap[i].w;"
        "      float nl = dot(lights.dirWrap[i].xyz, n);"
        "      light += clamp((nl + w) / ((w + 1.0) * (w + 1.0)), 0.0, 1.0) * lights.diffuse[i].rgb;"
        "    }"
        "  }"
        "  fragColor = vec4(toSrgb(vCol * light), 1.0);"
        "}")
    return gpu.shader.create_from_info(info)
shader = makeShader()

# Colours \/

linearCache = {} # ldraw code -> linear rgb
def codeLinear(code):
    rgb = linearCache.get(code)
    if rgb is None:
        entry = (colors.getColMap() or {}).get(str(code))
        srgb = colors.hexToRgb(entry["col"]) if (entry and code != 16) else DEFAULT_SRGB
        rgb = tuple(colors.srgbToLinear(c) for c in srgb)
        linearCache[code] = rgb
    return rgb

def codesToLinear(codes):
    # (n,) ldraw codes -> (n, 3) linear rgb
    unique, inverse = np.unique(codes, return_inverse=True)
    palette = np.array([codeLinear(int(c)) for c in unique], np.float32).reshape(-1, 3)
    return palette[inverse.ravel()]

# Mesh arrays \/

meshCache = {} # mesh name -> (co, nor, faceRgb, inherit, tris), all per corner
def getMeshArrays(mesh):
    # per corner positions, normals and colours plus corner triangles for a mesh, cached
    cached = meshCache.get(mesh.name)
    if cached is not None:
        return cached
    mesh.calc_loop_triangles()
    nLoops, nPolys = len(mesh.loops), len(mesh.polygons)

    loopVerts = np.empty(nLoops, np.int32)
    mesh.loops.foreach_get("vertex_index", loopVerts)
    co = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get("co", co)
    nor = np.empty(nLoops * 3, np.float32)
    mesh.corner_normals.foreach_get("vector", nor)

    starts = np.empty(nPolys, np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    totals = np.empty(nPolys, np.int32)
    mesh.polygons.foreach_get("loop_total", totals)
    # polygon of each corner
    loopPoly = np.repeat(np.arange(nPolys), totals)
    if nPolys and np.any(np.diff(starts) < 0): # corners not in polygon order (rare)
        loopPoly = np.empty(nLoops, np.int64)
        for p in range(nPolys):
            loopPoly[starts[p]:starts[p] + totals[p]] = p

    codes = np.full(nPolys, 16, np.int32)
    attr = mesh.attributes.get("col")
    if attr is not None:
        attr.data.foreach_get("value", codes)
    loopCodes = codes[loopPoly]
    inherit = loopCodes == 16
    faceRgb = codesToLinear(loopCodes) if nLoops else np.zeros((0, 3), np.float32)

    tris = np.empty(len(mesh.loop_triangles) * 3, np.int32)
    mesh.loop_triangles.foreach_get("loops", tris)

    cached = (co.reshape(-1, 3)[loopVerts], nor.reshape(-1, 3), faceRgb, inherit,
              tris.reshape(-1, 3).astype(np.int64))
    meshCache[mesh.name] = cached
    return cached

# Cloud batches \/

cloudBatches = {} # object name -> merged batch, or None for nothing to draw
def buildCloudBatch(obj):
    # one world space batch of every brick in a cloud
    pc = obj.data
    if len(pc.points) == 0:
        return None
    coll = obj.lego_prototypes
    if coll is None:
        return None

    protos = {common.typeFromName(o.name): o for o in coll.all_objects if o.type == 'MESH'}
    types = common.readTypes(pc)
    brickRgb = codesToLinear(colorOps.readPointAttr(pc, "col", np.int32, 16))
    worlds = common.instanceWorlds(np.array(obj.matrix_world),
                                   common.readPositions(pc),
                                   common.readRotations(pc)).astype(np.float32)

    allPos, allNor, allCol, allIdx, offset = [], [], [], [], 0
    for t in np.unique(types):
        proto = protos.get(int(t))
        if proto is None:
            continue
        co, nor, faceRgb, inherit, tris = getMeshArrays(proto.data)
        if len(tris) == 0:
            continue
        sel = types == t
        M = worlds[sel] @ np.array(proto.matrix_world, np.float32) # (k, 4, 4)
        R = M[:, :3, :3]
        k, L = len(M), len(co)

        allPos.append((np.einsum('kij,lj->kli', R, co) + M[:, None, :3, 3]).reshape(-1, 3))
        allNor.append(np.einsum('kij,lj->kli', R, nor).reshape(-1, 3))
        col = np.broadcast_to(faceRgb, (k, L, 3)).copy()
        col[:, inherit] = brickRgb[sel][:, None, :]
        allCol.append(col.reshape(-1, 3))
        starts = offset + np.arange(k, dtype=np.int64)[:, None, None] * L
        allIdx.append((tris[None] + starts).reshape(-1, 3))
        offset += k * L

    if not allPos:
        return None
    return batch_for_shader(shader, 'TRIS', {
        "pos": np.concatenate(allPos).astype(np.float32),
        "nor": np.concatenate(allNor).astype(np.float32),
        "col": np.concatenate(allCol).astype(np.float32),
    }, indices=np.concatenate(allIdx).astype(np.int32))

# Lighting \/

lightUbo = None
dummyTexture = None
matcapImages = {} # matcap path -> bpy image
pendingMatcaps = set()

def findStudioLight(context, name, kind):
    for sl in context.preferences.studio_lights:
        if sl.type == kind and sl.name == name:
            return sl
    return None

def requestMatcap(path):
    # images can't be loaded from a draw callback, so load on a timer and redraw
    if path in pendingMatcaps or path in matcapImages:
        return
    pendingMatcaps.add(path)
    def load():
        try:
            matcapImages[path] = bpy.data.images.load(path, check_existing=True)
        except Exception as e:
            print(f"LEGO EDITOR, WARNING: couldn't load matcap '{path}': {e}")
        pendingMatcaps.discard(path)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return None
    bpy.app.timers.register(load)

def getMatcapTexture(context, shading):
    sl = findStudioLight(context, shading.studio_light, 'MATCAP')
    if sl is None or not sl.path:
        return None
    img = matcapImages.get(sl.path)
    if img is None:
        requestMatcap(sl.path)
        return None
    try:
        return gpu.texture.from_image(img)
    except (ReferenceError, RuntimeError):
        matcapImages.pop(sl.path, None) # image was removed
        return None

def lightingData(context, shading, rv3d):
    # (40,) float32 LightData for the shader, and the matcap texture if matcap is in use
    dirWrap = np.zeros((4, 4), np.float32)
    diffuse = np.zeros((4, 4), np.float32)
    ambient = np.zeros(4, np.float32)
    mode = MODE_STUDIO
    texture = None

    if shading.light == 'FLAT':
        mode = MODE_FLAT
    elif shading.light == 'MATCAP':
        texture = getMatcapTexture(context, shading)
        mode = MODE_MATCAP if texture is not None else MODE_STUDIO

    if mode == MODE_STUDIO:
        sl = findStudioLight(context, shading.studio_light, 'STUDIO')
        if sl is not None:
            ambient[:3] = sl.light_ambient
            worldSpace = shading.use_world_space_lighting
            if worldSpace:
                # approximation of Workbench's world space studio lights
                toView = rv3d.view_matrix.to_3x3() @ Matrix.Rotation(shading.studiolight_rotate_z, 3, 'Z')
            for i, light in enumerate(list(sl.solid_lights)[:4]):
                if not light.use:
                    continue
                d = Vector(light.direction)
                if worldSpace:
                    d = toView @ Vector((d.x, -d.z, d.y))
                d.normalize()
                dirWrap[i] = (*d, light.smooth)
                diffuse[i, :3] = light.diffuse_color
        else:
            # no studio light found: one headlight
            ambient[:3] = 0.2
            dirWrap[0] = (0.0, 0.0, 1.0, 0.0)
            diffuse[0, :3] = 0.8

    data = np.concatenate([dirWrap.ravel(), diffuse.ravel(), ambient,
                           np.array([mode, 0.0, 0.0, 0.0], np.float32)])
    return data, texture

def bindLighting(context, shading, rv3d):
    global lightUbo, dummyTexture
    data, texture = lightingData(context, shading, rv3d)
    if lightUbo is None:
        lightUbo = gpu.types.GPUUniformBuf(data)
    else:
        lightUbo.update(data)
    if texture is None:
        if dummyTexture is None:
            dummyTexture = gpu.types.GPUTexture((1, 1), format='RGBA16F',
                                                data=gpu.types.Buffer('FLOAT', 4, [1.0] * 4))
        texture = dummyTexture
    shader.uniform_block("lights", lightUbo)
    shader.uniform_sampler("matcap", texture)

# Drawing \/

def draw():
    context = bpy.context
    space, rv3d = context.space_data, context.region_data
    if space is None or space.type != 'VIEW_3D' or rv3d is None:
        return
    shading = space.shading
    if shading.type != 'SOLID' or shading.show_xray:
        return

    batches = []
    for obj in context.visible_objects:
        if not common.isLegoPointCloud(obj):
            continue
        if obj.name not in cloudBatches:
            cloudBatches[obj.name] = buildCloudBatch(obj)
        batch = cloudBatches[obj.name]
        if batch is not None:
            batches.append(batch)
    if not batches:
        return

    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(True)
    gpu.state.blend_set('NONE')
    shader.bind()
    shader.uniform_float("viewProj", rv3d.perspective_matrix)
    shader.uniform_float("viewMat", rv3d.view_matrix)
    bindLighting(context, shading, rv3d)
    for batch in batches:
        batch.draw(shader)
    gpu.state.depth_test_set('NONE')

# Cache invalidation \/

@bpy.app.handlers.persistent
def onDepsgraphUpdate(scene, depsgraph):
    # rebuild only the clouds that changed; prototype changes rebuild everything
    if not cloudBatches:
        return
    for update in depsgraph.updates:
        id = getattr(update.id, "original", update.id)
        if isinstance(id, bpy.types.Object):
            cloudBatches.pop(id.name, None)
        elif isinstance(id, bpy.types.PointCloud):
            for name in [n for n in cloudBatches
                         if (o := bpy.data.objects.get(n)) is not None and o.data == id]:
                cloudBatches.pop(name, None)
        elif isinstance(id, (bpy.types.Mesh, bpy.types.Collection)):
            cloudBatches.clear()
            return

def clearCaches():
    # call when prototypes are re-imported or the colour map is rebuilt
    cloudBatches.clear()
    meshCache.clear()
    linearCache.clear()

def register():
    global handle
    handle = bpy.types.SpaceView3D.draw_handler_add(draw, (), 'WINDOW', 'POST_VIEW')
    bpy.app.handlers.depsgraph_update_post.append(onDepsgraphUpdate)

def unregister():
    global handle, lightUbo, dummyTexture
    if onDepsgraphUpdate in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(onDepsgraphUpdate)
    if handle:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
        handle = None
    clearCaches()
    matcapImages.clear()
    pendingMatcaps.clear()
    lightUbo = None
    dummyTexture = None
