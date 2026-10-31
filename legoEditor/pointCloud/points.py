"""
The purpose of this file is to allow creating/extension of point cloud objects
It calls ldraws importer to get mesh data for the bricks, then creates objects in the correct order
for instancing on to the points by type in geo nodes
the order is in name order, so we prefix object names with a unique index.
Any points added to the point cloud then just get an index+1 value in the point attibute and matching index
Model = collection of points categorized together, default = misc
hopefully that makes some sense :)
"""
import bpy

import numpy as np

from ..ldraw.importer import importer

def importAsPoint(brickId, model="misc"):
    # method to create or extend the current models point cloud
    if not brickId.lower().endswith(".dat"):
        brickId = brickId + ".dat"

    brickMesh = importer.getBrickMesh(brickId)

    # get a collection for prototypes for this model, if it doesnt exist, make it
    protoCollName = model + "_Prototypes"
    existing = bpy.data.collections.get(protoCollName)
    if existing:
        protoColl = existing
    else:
        protoColl = bpy.data.collections.new(protoCollName)
        bpy.context.scene.collection.children.link(protoColl)

    # get the point cloud
    currentPc = bpy.data.objects.get(model,None)
    if (currentPc is None) or (currentPc.type != 'POINTCLOUD'):
        pc = bpy.data.pointclouds.new(model)
        pcObj = bpy.data.objects.new(model,pc)
        bpy.context.scene.collection.objects.link(pcObj)
    else:
        pcObj = currentPc
        pc = pcObj.data

    # make the new point
    currentAmount = len(pc.points)
    pc.resize(currentAmount + 1)
    newIndex = currentAmount - 1

    # Read type attribute
    typeAttr = None
    if currentAmount > 0:
        typeAttr = pc.attributes.get("type",None)
        if typeAttr is None:
            nextFreeType = 0
        else:
            typesAmount = len(typeAttr.data)
            if typesAmount > 0:
                typeVals = np.empty(typesAmount, dtype=np.int32)
                typeAttr.data.foreach_get("value",typeVals)
                nextFreeType = int(typeVals.max()) + 1
            else:
                nextFreeType = 0
    else:
        nextFreeType = 0

    # 3. check if the brickId already exists as a protoype, and if so, set that type
    prototype = next((o for o in protoColl.all_objects if o.data == brickMesh), None)
    if prototype is None:
        prototype = importer.getBrickObj(brickMesh)
        protoColl.objects.link(prototype)

    prototype.name = f"{nextFreeType:05d}"
    if typeAttr is None:
        typeAttr = pc.attributes.new("type","INT","POINT")
    typeAttr.data[newIndex].value = nextFreeType
    
    # get the current max brick type index being used, and add 1 for the new brick index
    # this will define the prototype prefix on the brick object

def importAsModel(brickIdList,modeName):
    # method to import a list of bricks as a model
    pass
