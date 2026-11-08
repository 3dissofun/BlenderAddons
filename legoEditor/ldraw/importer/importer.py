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

from . import brickMaker, blendLibrary, parseMpd, library
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
    allModels = parseMpd.parseMpd(mpdPath)
    modelNames, partNames, tables = parseMpd.buildModelTables(allModels)

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
            brickObj = bpy.data.objects.new(f"{i:05d}", brickMesh)
            protoColl.objects.link(brickObj)

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

def getBrickObj(brickMesh):
    # returns an object for a lego piece given a mesh
    # sets relevant attributes/transforms etc (but not for now...)
    obj = bpy.data.objects.new(brickMesh.name,brickMesh)
    obj.is_lego = True
    return obj

def getBrickMesh(brickId,mode="auto"):
    # returns a bpy mesh struct for a mesh given a brick ID
    # mode defines operation
        # mode="auto": first try the local file, then blender library, then source
        # mode="src": ignore local data, build the mesh fresh from source
        # mode="lib": get the brick from the library
        # mode="local": get the brick from the current file
    # if the brick cannot be found in the given mode, None is returned

    auto = bool(mode == "auto")
    src = bool(mode == "src")
    lib = bool(mode == "lib")
    local = bool(mode == "local")

    if not brickId.lower().endswith(".dat"):
        brickId = brickId + ".dat"

    # 1. local file, fastest
    if auto or local:
        existingMesh = bpy.data.meshes.get(brickId)
        if existingMesh:
            return existingMesh
        if local:
            # local cancels here, auto continues
            return None

    #2. library file
    if auto or lib:
        libMesh = blendLibrary.getLibMesh(brickId)
        if libMesh:
            return libMesh
        if lib:
            return None

    #3. build from source
    if auto or src:
        srcMesh = brickMaker.makeBrickMesh(brickId)
        if srcMesh:
            return srcMesh    
    return None

def getBrickMeshes(brickIdList,mode="auto"):
    # sister function of getBrickMesh, modes work same way, just in bulk
    # returns a list of bpy mesh structs

    # ensure names end in .dat
    brickIdList = [name if name.lower().endswith(".dat") else f"{name}.dat" for name in brickIdList]
    numOfBricks = len(brickIdList)

    output = []
    missing = None # list to track missing meshes if any step creates any misses
    # 1. local file, fastest
    if auto or local:
        localFound = [mesh for mesh in bpy.data.meshes if mesh in brickIdList]
        if len(localFound) == numOfBricks:
            # were done
            return localFound
        missing = [mesh for mesh in brickIdList if mesh not in localFound]
        output.extend(localFound)

    #2. library file
    if auto or lib:
        if missing:
            seach = missing
        else:
            search = brickIdList
        libFound = blendLibrary.getLibMeshes(search)
        output.extend(libFound)
        if len(output) == numOfBricks:
            # were done
            return output
        missing = [mesh for mesh in brickIdList if mesh not in output]

    #3. build from source
    if auto or src:
        for brickName in brickIdList:
            brickMesh = brickMaker.makeBrickMesh(brickId)
            output.append(brickMesh)

    return output
