import bpy
import hashlib

# Block Hashers

def hashMesh(block):
    hasher = hashlib.sha256()

    for v in block.vertices:
        hasher.update(str(v.index).encode('utf-8'))
        # Round to avoid floating point noise causing false mismatches
        co = v.co
        hasher.update(f"{co.x:.6f},{co.y:.6f},{co.z:.6f}".encode('utf-8'))

    for e in block.edges:
        hasher.update(str(e.index).encode('utf-8'))
        hasher.update(str(tuple(e.vertices)).encode('utf-8'))

    for p in block.polygons:
        hasher.update(str(p.index).encode('utf-8'))
        hasher.update(str(tuple(p.vertices)).encode('utf-8'))

    return hasher.hexdigest()

def hashMaterial(block):
    
    return ""

def hashAction(block):
    
    return ""

def hashObject(block):

    return ""

def hashImage(block):

    return ""

def hashNodeGroup(block):

    return ""

def hashArmature(block):

    return ""

# Sub Hashers

def hashTransform(block):

    return ""

# Entry Point

blockHashes = {
        "objects": hashObject,
        "meshes": hashMesh,
        "materials": hashMaterial,
        "images": hashImage,
        "node_groups": hashNodeGroup,
        "armatures": hashArmature,
        "actions": hashAction,
        }


def hashBlock(block,blockType):
    hasher = blockHashes.get(blockType)
    if hasher is None:
        print(f"HASHER: WARNING, No registered hash function for type {blockType}")
        return ""
    else:
        return hasher(block)
