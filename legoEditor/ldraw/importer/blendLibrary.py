"""
The intent of this file is to provide easy access to a cached blender library of ldraw pieces,
link or append data from there, in bulk (or as single)
"""
import bpy
from ... import utils

blenderLdrawLib = None

def getLibrary():
    global blenderLdrawLib
    libDir = utils.getPref("blenderLdrawLibrary")
    if libDir:
        blenderLdrawLib = libDir
        return libDir
    else:
        print("LDRAW IMPORTER: WARNING, Library get called but no library is set in addon preferences")
        return None

def getLibMeshes(meshList):
    # Load the library once and get all links/appends done in one go
    if not blenderLdrawLib:
        lib = getLibrary()
        if not lib:
            return []
    else:
        lib = blenderLdrawLib

    # open the library file and append
    return []

def getLibMesh(meshName):
    # This function shouldnt be called in bulk, but just for a single brick
    result = getLibMeshes([meshName])
    if result:
        return result[0]
    return None
