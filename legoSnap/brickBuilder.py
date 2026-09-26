# The purpose of this file is to convert a lego id into a completed object
# It uses the .dat lego parser module and it makes the bricks in bpy

import bpy
from . import datParser

def makeBrick(brickId):
    faces = datParser.flatten(datParser.resolveFile(f"{brickId}.dat"))
 
    verts = []
    vertIndex = {}
    polys = []
     
    for colour, points in faces:
        poly = []
        for x, y, z in points:
            key = (round(x, 3), round(y, 3), round(z, 3))
            if key not in vertIndex:
                vertIndex[key] = len(verts)
                verts.append((x * 0.02, z * 0.02, -y * 0.02))   # LDraw -Y up -> Blender Z up
            poly.append(vertIndex[key])
        polys.append(poly)
     
    mesh = bpy.data.meshes.new(brickId)
    mesh.from_pydata(verts, [], polys)
    mesh.validate()
    mesh.update()
     
    obj = bpy.data.objects.new(brickId, mesh)
    return obj
