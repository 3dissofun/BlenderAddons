# The purpose of this file is to convert a lego id into a completed object
# It uses the .dat lego parser module and it makes the bricks in bpy

import bpy
from . import datParser

from mathutils import Matrix

brickSf = 0.02 # for real world scale
LDtoBL = Matrix(((1, 0, 0), # Transform matrix to convert between LDraw and Blender axis
                   (0, 0, 1),
                   (0, -1, 0))) # same axis swap as your verts: (x, z, -y)

def makeBrick(brickId):
    faces, snaps = datParser.flatten(datParser.resolveFile(f"{brickId}.dat"))
    if not faces:
        return None
 
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
     
    for kind, attrs in snaps:
        snap = mesh.lego_snaps.add()
        snap.kind = kind
        snap.gender = attrs.get("gender","")
        snap.caps = attrs.get("caps", "")
        snap.secs = attrs.get("secs", "")

        x,y,z = attrs["worldPos"]
        snap.location = (x*brickSf, z*brickSf, -y*brickSf)

        ori = Matrix(attrs["worldOri"].tolist())
        blOri = LDtoBL @ ori @ LDtoBL.transposed()
        snap.rotation = blOri.normalized().to_quaternion()

    obj = bpy.data.objects.new(brickId, mesh)
    return obj
