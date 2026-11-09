"""
The intent of this file is to give easy function calls for getting/making bricks
Getting a brick means bringing mesh data from the current file, and attaching it
to either an object or a point cloud depending on the mode
Making a brick means calling brickMaker.py and building the brick from source .dat files
"""
import bpy

import numpy as np
from math import radians
from mathutils import Matrix
import tempfile
from pathlib import Path
import time

from . import brickMaker, parseMpd, library, blobReader
from ...pointCloud import points
from ... import utils

def unpackMpd(allModels, outDir):
    # Writes each embedded part block to outDir, using its normalised name as the path
    for name, lines in allModels.items():
        if not name.endswith(".dat"):
            continue
        path = Path(outDir) / name  # "s/10243 - 60616s01.dat" -> s/ subfolder
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(" ".join(tokens) for tokens in lines) + "\n", encoding="utf-8")

def importMpd(mpdPath, collection, scale=1.0):
    startTime = time.perf_counter()
    allModels = parseMpd.parseMpd(mpdPath)
    modelNames, partNames, tables = parseMpd.buildModelTables(allModels)

    overlay = tempfile.mkdtemp(prefix="ldraw_mpd_")
    unpackMpd(allModels, overlay)
    library.setOverlay(overlay)
    try:
        protoColl = bpy.data.collections.new(f"{modelNames[0]}_Prototypes")
        for i, part in enumerate(partNames):
            brickMesh = getBrickMesh(part)
            if brickMesh is None:
                print(f"LDRAW: WARNING, could not build {part}, using an empty placeholder")
            brickObj = getBrickObj(brickMesh,name=f"{i:05d}")
            protoColl.objects.link(brickObj)

        duration = time.perf_counter() - startTime
        print(f"MAKE took {duration*1000}ms")
        # Stage 1: each model's point cloud data, built once (index = model id)
        pcData = [points.makePointCloudData(modelNames[i], t) for i, t in enumerate(tables)]

        # Stage 2: one object per placement, parented down the tree
        def place(modelId, parentObj, matrix, colour, stepOffset):
            pcObj, modifier = points.getNewPc(modelNames[modelId], pcData[modelId], protoColl)
            collection.objects.link(pcObj)
            pcObj.parent = parentObj
            pcObj.matrix_basis = Matrix(matrix.tolist())  # relative to the parent

            # Once the node group has these inputs:
            # utils.setModifierInput(modifier, "InheritColour", colour)
            # utils.setModifierInput(modifier, "StepOffset", stepOffset)

            t = tables[modelId]
            isModel = t["kind"]
            for childId, m, c, s in zip(t["index"][isModel], t["matrix"][isModel],
                                        t["colour"][isModel], t["step"][isModel]):
                childColour = colour if c == 16 else int(c)  # 16 = take the parent's colour
                place(int(childId), pcObj, m, childColour, stepOffset + int(s))
            return pcObj

        root = place(0, None, np.eye(4), 16, 0)

        # LDraw is -Y up, Blender is Z up
        root.matrix_basis = Matrix.Rotation(radians(-90), 4, 'X') @ Matrix.Scale(scale, 4)
    finally:
        library.setOverlay(None)
    return root

def getBrickObj(brickMesh,name=None):
    # returns an object for a lego piece given a mesh
    # sets relevant attributes/transforms etc (but not for now...)
    obj = bpy.data.objects.new(brickMesh.name if not name else name,brickMesh)
    obj.is_lego = True
    return obj

def getBrickMesh(brickId):
    # returns a bpy mesh struct for a mesh given a brick ID
    # first checks the binary blobs, if that doesnt exist, checks the .dat files
    # if the brick cannot be found, None is returned

    blobMesh = blobReader.buildMesh(brickId)
    if blobMesh:
        return blobMesh
    
    # fallback option for custom parts etc
    return brickMaker.makeBrickMesh(brickId)
