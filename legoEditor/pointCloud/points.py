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

from ..ldraw.importer import importer

def importAsPoint(brickId, model="misc"):
    # method to create or extend the current models point cloud

    # get the mesh data first

    # make the point with relevant attribute
    currentPc = bpy.data.objects.get(model,None)
    if (currentPc is None) or (currentPc.type != 'POINTCLOUD'):
        pcObj = bpy.data.objects.new(model)
    else:
        pcObj = currentPc

    pc = pcObj.data
    currentAmount = len(pc.points)
    pc.resize(currentAmount + 1)
