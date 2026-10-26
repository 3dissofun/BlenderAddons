import bpy
import bmesh

import numpy as np
from mathutils import Matrix
from pathlib import Path
import json
import tempfile

from . import parser, library

SHADER_FILE = Path(__file__).parent / "shaders.blend"
SHADER_GROUP = "LEGO_SN"

# LDraw -> Blender axes (x, z, -y). Scaled copy lives in LD_TO_BL.
LD_AXES = np.array([
    [1, 0,  0, 0],
    [0, 0,  1, 0],
    [0, -1, 0, 0],
    [0, 0,  0, 1],
], dtype=np.float64)

BRICK_SF = 0.02
MERGE_DIST = 0.05 * BRICK_SF
LD_TO_BL = None

def setScale(sf):
    # LDraw units -> Blender units. Rebuilds everything derived from the scale.
    global BRICK_SF, MERGE_DIST, LD_TO_BL
    BRICK_SF = sf
    MERGE_DIST = 0.05 * sf
    LD_TO_BL = LD_AXES.copy()
    LD_TO_BL[:3, :3] *= sf

setScale(BRICK_SF)
# ---------------------------------------------------------------------------
# Snaps
# ---------------------------------------------------------------------------
 
def snapToBlender(matrix):
    # LDraw snap matrix (LDU, top-level part space) -> mesh local space.
    # Positions get the full LD_TO_BL (axes + scale); the rotation keeps only the
    # axis swap so the stored matrix stays orthonormal.
    m = LD_TO_BL @ matrix
    m[:3, :3] /= BRICK_SF
    return m
 
def storeSnaps(mesh, snaps):
    # Write parser snaps (shadow.Snap, LDU) onto mesh.lego_snaps in Blender units
    coll = mesh.lego_snaps
    coll.clear()
    for s in snaps:
        item = coll.add()
        item.kind = s.kind[5:]               # "SNAP_CYL" -> "CYL"
        item.gender = s.gender
        item.group = s.group
        item.ids = ",".join(sorted(s.ids))
        item.matrix = snapToBlender(s.matrix).ravel().tolist()
        item.capA, item.capB = s.capA, s.capB
        item.center, item.slide = s.center, s.slide
        item.radius = s.radius * BRICK_SF
        item.length = s.length * BRICK_SF
        item.bounding = " ".join(s.bounding)
        item.extra = json.dumps(s.extra) if s.extra else ""
        for shape, radius, length in s.secs:
            sec = item.secs.add()
            sec.shape = shape
            sec.radius = radius * BRICK_SF
            sec.length = length * BRICK_SF

# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def getShaderGroup():
    # Append the LEGO_SN node group from shaders.blend once, then reuse it
    group = bpy.data.node_groups.get(SHADER_GROUP)
    if group:
        return group
 
    if not SHADER_FILE.is_file():
        print(f"LDRAW IMPORTER: ERROR, shader file not found: '{SHADER_FILE}'")
        return None
 
    with bpy.data.libraries.load(str(SHADER_FILE), link=False) as (src, dst):
        if SHADER_GROUP in src.node_groups:
            dst.node_groups = [SHADER_GROUP]
        else:
            print(f"LDRAW IMPORTER: ERROR, node group '{SHADER_GROUP}' not found in '{SHADER_FILE}'")
 
    return bpy.data.node_groups.get(SHADER_GROUP)
 
def _newMaterial(name):
    # Fresh node material; returns (material, nodes, links, group node, col attribute node)
    # groupNode is None if the node group couldn't be loaded, in which case the
    # material falls back to a plain principled BSDF driven by the colour attribute.
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
 
    attr = nodes.new("ShaderNodeAttribute")
    attr.attribute_type = 'GEOMETRY'
    attr.attribute_name = "col"
 
    group = getShaderGroup()
    if group is None:
        attr.location = (bsdf.location.x - 500, bsdf.location.y)
        links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
        return mat, nodes, links, None, attr
 
    groupNode = nodes.new("ShaderNodeGroup")
    groupNode.node_tree = group
    groupNode.location = bsdf.location
    attr.location = (groupNode.location.x - 500, groupNode.location.y)
 
    # "col" is an int attribute, which the Attribute node exposes as Fac (float)
    codeSocket = groupNode.inputs["Col Code"]
    links.new(attr.outputs["Fac" if codeSocket.type == 'VALUE' else "Color"], codeSocket)
 
    # Shader output goes straight to the surface; anything else feeds Base Color
    out = groupNode.outputs[0]
    if out.type == 'SHADER':
        matOut = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL')
        links.new(out, matOut.inputs["Surface"])
        nodes.remove(bsdf)
    else:
        links.new(out, bsdf.inputs["Base Color"])
 
    return mat, nodes, links, groupNode, attr
 
def getSharedMaterial():
    # One material for every untextured face of every brick
    mat = bpy.data.materials.get("LEGO_MAT")
    if mat is None:
        mat = _newMaterial("LEGO_MAT")[0]
    return mat
 
def makeTexturedMaterial(name, texPaths):
    # One material for a whole textured brick. The "tex" face attribute selects
    # which texture (if any) each face shows. The textures are merged into one
    # sticker colour (black where no texture applies) and fed to the node group's
    # "Sticker Col" input. If the group also has a "Sticker Alpha" input, it gets
    # the merged texture alpha, so the group can tell sticker pixels from bare ones.
    mat, nodes, links, groupNode, colAttr = _newMaterial(name)
    if groupNode is None:
        return mat  # no node group, so there is no sticker input to drive
 
    x0, y0 = groupNode.location.x - 300 * (len(texPaths) + 1), groupNode.location.y
    colAttr.location = (x0, y0)
 
    texAttr = nodes.new("ShaderNodeAttribute")
    texAttr.attribute_type = 'GEOMETRY'
    texAttr.attribute_name = "tex"
    texAttr.location = (x0, y0 - 250)
 
    colour = None  # running sticker colour
    alpha = None   # running sticker alpha
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
 
        # image alpha on matching faces only
        fac = nodes.new("ShaderNodeMath")
        fac.operation = 'MULTIPLY'
        fac.location = (x, y0 - 100)
        links.new(img.outputs["Alpha"], fac.inputs[0])
        links.new(match.outputs[0], fac.inputs[1])
 
        # Layer this texture over the previous ones (black underneath the first)
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = 'RGBA'
        mix.location = (x, y0 + 150)
        links.new(fac.outputs[0], mix.inputs["Factor"])
        if colour is None:
            mix.inputs[6].default_value = (0.0, 0.0, 0.0, 1.0)
        else:
            links.new(colour, mix.inputs[6])           # A (previous stickers)
        links.new(img.outputs["Color"], mix.inputs[7]) # B (this texture)
        colour = mix.outputs[2]
 
        if alpha is None:
            alpha = fac.outputs[0]
        else:
            best = nodes.new("ShaderNodeMath")
            best.operation = 'MAXIMUM'
            best.location = (x, y0 - 100)
            links.new(alpha, best.inputs[0])
            links.new(fac.outputs[0], best.inputs[1])
            alpha = best.outputs[0]
 
    links.new(colour, groupNode.inputs["Sticker Col"])
    links.new(alpha, groupNode.inputs["Sticker Alpha"])
    useSticker = groupNode.inputs.get("Use Sticker")
    if useSticker is not None:
        useSticker.default_value = True

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
        newObj = bpy.data.objects.new(brickId, mesh)
        newObj.is_lego = True
        return newObj

    relPath = brickId if brickId.lower().endswith((".dat", ".ldr")) else brickId + ".dat"
    partPath = library.resolvePart(relPath)
    if partPath is None:
        print(f"LDRAW IMPORTER: ERROR, Brick '{brickId}' not found in part library")
        return None

    data = parser.parsePart(str(partPath), col=colId, matrix=LD_TO_BL)
    mesh = bpy.data.meshes.new(meshName)
    obj = bpy.data.objects.new(brickId, mesh)
    obj.is_lego = True
    storeSnaps(mesh, data["connectors"])

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

# MODELS/ MULTI BRICK IMPORTS

def _readModels(path):
    # {model name (lowercase): [lines]}. The first entry is the main model.
    # A plain .ldr (no "0 FILE" lines) becomes a single model.
    default = Path(path).name.lower()
    models, cur = {default: []}, default
    with open(path, encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            t = line.split(maxsplit=2)
            if t[:2] == ["0", "FILE"] and len(t) > 2:
                if not models.get(default):
                    models.pop(default, None)   # nothing came before the first FILE
                cur = t[2].strip().lower().replace("\\", "/")
                models[cur] = []
            elif t[:2] == ["0", "NOFILE"]:
                cur = None
            elif cur is not None and line:
                models[cur].append(line)
    return models

def _placements(models, name, parent=None, parentCol=16, depth=0):
    # Yields (part name, colour, 4x4 LDraw matrix), with sub-models expanded
    parent = np.eye(4) if parent is None else parent
    for line in models[name]:
        t = line.split()
        if len(t) < 15 or t[0] != "1":
            continue
        col = int(t[1])
        col = parentCol if col == 16 else col        # 16 inherits from the parent
        x, y, z, a, b, c, d, e, f, g, h, i = map(float, t[2:14])
        m = parent @ np.array([[a, b, c, x], [d, e, f, y], [g, h, i, z], [0, 0, 0, 1]])
        ref = " ".join(t[14:]).lower().replace("\\", "/")
        if ref in models and depth < 32:             # sub-model defined in this file
            yield from _placements(models, ref, m, col, depth + 1)
        else:                                        # library part
            yield ref, col, m

PART_TYPES = {"part", "subpart", "primitive", "8_primitive", "48_primitive", "shortcut"}

def _isPart(lines):
    # An MPD block is an embedded part (not a sub-model) if its header says so,
    # or failing that, if it contains geometry
    for line in lines:
        t = line.split()
        if t[:2] == ["0", "!LDRAW_ORG"] and len(t) > 2:
            return t[2].lower().removeprefix("unofficial_") in PART_TYPES
        if t and t[0] in ("2", "3", "4", "5"):
            return True
    return False

def importLdr(ldrPath, collection):
    fromBl = np.linalg.inv(LD_TO_BL)
    models = _readModels(ldrPath)
    if not any(models.values()):
        return []
    main = next(iter(models))
    parts = {n: ls for n, ls in models.items() if n != main and _isPart(ls)}
    subModels = {n: ls for n, ls in models.items() if n not in parts}

    objs = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, lines in parts.items():
            p = Path(tmp) / name                      # keeps s/, 48/ etc.
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        library.setOverlay(tmp)
        try:
            for ref, col, m in _placements(subModels, main):
                obj = makeBrick(ref, colId=col, forceNew=False)
                if obj is None:
                    continue
                obj.matrix_world = Matrix((LD_TO_BL @ m @ fromBl).tolist())
                collection.objects.link(obj)
                objs.append(obj)
        finally:
            library.setOverlay(None)
    return objs
