import bpy
import bmesh
import numpy as np

from . import parser, library

BRICK_SF = 0.02                    # LDraw units -> metres
MERGE_DIST = 0.05 * BRICK_SF       # merge-by-distance threshold, in Blender units

# LDraw -> Blender axes (x, z, -y), scaled to real-world size. Passed to the parser
# as the root matrix so points come out already in Blender space.
LD_TO_BL = np.array([
    [1, 0,  0, 0],
    [0, 0,  1, 0],
    [0, -1, 0, 0],
    [0, 0,  0, 1],
], dtype=np.float64)
LD_TO_BL[:3, :3] *= BRICK_SF

# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def _newMaterial(name):
    # Fresh node material; returns (material, nodes, links, principled bsdf, col attribute node)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')

    attr = nodes.new("ShaderNodeAttribute")
    attr.attribute_type = 'GEOMETRY'
    attr.attribute_name = "col"
    attr.location = (bsdf.location.x - 500, bsdf.location.y)
    return mat, nodes, links, bsdf, attr

def getSharedMaterial():
    # One material for every untextured face of every brick
    mat = bpy.data.materials.get("LEGO_MAT")
    if mat is None:
        mat, nodes, links, bsdf, attr = _newMaterial("LEGO_MAT")
        links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    return mat

def makeTexturedMaterial(name, texPaths):
    # One material for a whole textured brick. The "tex" face attribute selects
    # which texture (if any) each face shows; each is composited over the face
    # colour using its alpha. Faces with tex = -1 match nothing and keep the colour.
    mat, nodes, links, bsdf, colAttr = _newMaterial(name)
    x0, y0 = bsdf.location.x - 300 * (len(texPaths) + 1), bsdf.location.y
    colAttr.location = (x0, y0)

    texAttr = nodes.new("ShaderNodeAttribute")
    texAttr.attribute_type = 'GEOMETRY'
    texAttr.attribute_name = "tex"
    texAttr.location = (x0, y0 - 250)

    colour = colAttr.outputs["Color"]
    for i, texPath in enumerate(texPaths):
        x = x0 + 300 * (i + 1)

        img = nodes.new("ShaderNodeTexImage")
        img.image = bpy.data.images.load(texPath, check_existing=True)
        img.extension = 'CLIP'  # outside the projection, alpha = 0
        img.location = (x, y0 - 300)

        # 1 on faces whose tex index == i, else 0
        match = nodes.new("ShaderNodeMath")
        match.operation = 'COMPARE'
        match.inputs[1].default_value = i
        match.inputs[2].default_value = 0.5  # epsilon
        match.location = (x, y0 - 600)
        links.new(texAttr.outputs["Fac"], match.inputs[0])

        # mix factor = image alpha on matching faces only
        fac = nodes.new("ShaderNodeMath")
        fac.operation = 'MULTIPLY'
        fac.location = (x, y0 - 100)
        links.new(img.outputs["Alpha"], fac.inputs[0])
        links.new(match.outputs[0], fac.inputs[1])

        mix = nodes.new("ShaderNodeMix")
        mix.data_type = 'RGBA'
        mix.location = (x, y0 + 150)
        links.new(fac.outputs[0], mix.inputs["Factor"])
        links.new(colour, mix.inputs[6])               # A (colour)
        links.new(img.outputs["Color"], mix.inputs[7]) # B (colour)
        colour = mix.outputs[2]

    links.new(colour, bsdf.inputs["Base Color"])
    return mat

# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------

def makeBrick(brickId, colId=16, mergeVerts=True, forceNew=True):
    # brickId:  part number ("3001") or relative path ("3001.dat", "s/3001s01.dat")
    # colId:    LDraw colour code substituted for faces using 16 (main colour)
    # forceNew: build a fresh mesh from source instead of reusing an existing one

    # Colour 16 is baked into the attribute, so the cache key must include it
    meshName = f"{brickId}_{colId}"
    mesh = bpy.data.meshes.get(meshName)
    if mesh and not forceNew:
        return bpy.data.objects.new(brickId, mesh)

    relPath = brickId if brickId.lower().endswith((".dat", ".ldr")) else brickId + ".dat"
    partPath = library.resolvePart(relPath)
    if partPath is None:
        print(f"LDRAW IMPORTER: ERROR, Brick '{brickId}' not found in part library")
        return None

    data = parser.parsePart(str(partPath), col=colId, matrix=LD_TO_BL)
    mesh = bpy.data.meshes.new(meshName)
    obj = bpy.data.objects.new(brickId, mesh)
    obj.is_lego = True

    nFaces = len(data["faceQuads"])
    if nFaces == 0:
        print(f"LDRAW IMPORTER: WARNING, part '{brickId}' has no geometry")
        return obj

    # Geometry: every face has its own verts, in loop order
    pts = np.asarray(data["points"], dtype=np.float32).reshape(-1, 3)
    loopTotals = np.where(np.asarray(data["faceQuads"], dtype=bool), 4, 3)
    loopStarts = np.concatenate(([0], np.cumsum(loopTotals)[:-1]))
    mesh.from_pydata(pts, [], [range(s, s + t) for s, t in zip(loopStarts, loopTotals)])

    # Colour codes as an integer face attribute
    colAttr = mesh.attributes.new("col", 'INT', 'FACE')
    colAttr.data.foreach_set("value", np.asarray(data["faceCols"], dtype=np.int32))

    # Materials: untextured bricks share one material; textured bricks get their
    # own, with every texture they use, selected per face by the "tex" attribute
    faceTex = np.asarray(data["faceTex"], dtype=np.int32)
    if (faceTex >= 0).any():
        used = np.unique(faceTex[faceTex >= 0])  # global texture indices this brick uses
        localTex = np.where(faceTex >= 0, np.searchsorted(used, faceTex), -1).astype(np.int32)
        mesh.attributes.new("tex", 'INT', 'FACE').data.foreach_set("value", localTex)
        texPaths = [data["textures"][t] for t in used]
        mesh.materials.append(makeTexturedMaterial(f"{meshName}_tex", texPaths))
    else:
        mesh.materials.append(getSharedMaterial())

    # UVs (loops are in the same order as the parser's points)
    if (faceTex >= 0).any():
        uvs = np.asarray(data["faceUvs"], dtype=np.float32).reshape(-1, 2)
        texLoops = np.repeat(faceTex >= 0, loopTotals)
        uvs[texLoops, 1] = 1.0 - uvs[texLoops, 1]  # LDraw image origin is top-left
        mesh.uv_layers.new(name="UVMap").data.foreach_set("uv", uvs.ravel())

    # Weld the per-face verts so shading behaves (UVs and face attributes survive)
    if mergeVerts:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=MERGE_DIST)
        bm.to_mesh(mesh)
        bm.free()

    mesh.validate()
    mesh.update()
    return obj
