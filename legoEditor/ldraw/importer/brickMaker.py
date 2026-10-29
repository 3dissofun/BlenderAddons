"""
The intent of this file is to import mesh data using the parser and then return it as a bpy mesh struct
that can be later applied to an object etc
Bricks are returned at native LDU axis/scale conventions
"""
import bpy
import bmesh

import numpy as np

from . import parser, library, materials

def storeSnaps(mesh, snaps):
    coll = mesh.lego_snaps
    coll.clear()
    for s in snaps:
        item = coll.add()
        item.kind = s.kind[5:] # "SNAP_CYL" -> "CYL"
        item.gender = s.gender
        item.group = s.group
        item.ids = ",".join(sorted(s.ids))
        item.matrix = s.matrix.ravel()
        item.capA = s.capA
        item.capB = s.capB
        item.center, item.slide = s.center, s.slide
        item.radius = s.radius
        item.length = s.length
        item.bounding = " ".join(s.bounding)
        item.extra = ""
        # item.extra = json.dumps(s.extra) if s.extra else ""
        for shape, radius, length in s.secs:
            sec = item.secs.add()
            sec.shape = shape
            sec.radius = radius
            sec.length = length

def makeBrickMesh(brickId):
    # Takes a brick id such as 3001.dat(or 3001)
    # Returns a bpy mesh structure
    if not brickId.lower().endswith(".dat"):
        brickId = brickId + ".dat"

    brickPath = library.resolvePart(brickId)
    if brickPath is None:
        print(f"LDRAW IMPORTER: ERROR, Brick '{brickId}' not found in part library")
        return None

    # Get raw data from the parser
    brickData = parser.parsePart(str(brickPath),col=16)

    mesh = bpy.data.meshes.new(brickId)
    storeSnaps(mesh, brickData["connectors"])

    faceQuads = brickData["faceQuads"]
    faceCount = len(faceQuads)
    if faceCount == 0:
        print(f"LDRAW IMPORTER: WARNING, part '{brickId}' has no geometry")
        return mesh 

    # Main mesh build
    points = np.asarray(brickData["points"], dtype=np.float32).reshape(-1, 3)
    loopTotals = np.where(np.asarray(faceQuads, dtype=bool), 4, 3)
    loopStarts = np.concatenate(([0], np.cumsum(loopTotals)[:-1]))
    mesh.from_pydata(points, [], [range(s, s + t) for s, t in zip(loopStarts, loopTotals)])

    # Colour codes as an integer face attribute
    # NOTE the color codes are baked into meshes, so reused across meshes,
    # but the color code 16 indicates it is variable so can be set using various
    # methods elswhere outside this function
    colAttr = mesh.attributes.new("col", 'INT', 'FACE')
    colAttr.data.foreach_set("value", np.asarray(brickData["faceCols"], dtype=np.int32))

    # Materials: untextured bricks share one material; textured bricks get their
    # own, with every texture they use, selected per face by the "tex" attribute
    faceTex = np.asarray(brickData["faceTex"], dtype=np.int32)
    if (faceTex >= 0).any():
        used = np.unique(faceTex[faceTex >= 0])  # global texture indices this brick uses
        localTex = np.where(faceTex >= 0, np.searchsorted(used, faceTex), -1).astype(np.int32)
        mesh.attributes.new("tex", 'INT', 'FACE').data.foreach_set("value", localTex)
        texPaths = [brickData["textures"][t] for t in used]
        mesh.materials.append(materials.makeTexturedMaterial(f"{brickId}_tex", texPaths))
    else:
        mesh.materials.append(materials.getSharedMaterial())

    # UVs (loops are in the same order as the parser's points)
    if (faceTex >= 0).any():
        uvs = np.asarray(brickData["faceUvs"], dtype=np.float32).reshape(-1, 2)
        texLoops = np.repeat(faceTex >= 0, loopTotals)
        uvs[texLoops, 1] = 1.0 - uvs[texLoops, 1]  # LDraw image origin is top-left
        mesh.uv_layers.new(name="UVMap").data.foreach_set("uv", uvs.ravel())

    # Weld the per-face verts so shading behaves (UVs and face attributes survive)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.05)
    bm.to_mesh(mesh)
    bm.free()

    mesh.validate()
    mesh.update()
    return mesh 
