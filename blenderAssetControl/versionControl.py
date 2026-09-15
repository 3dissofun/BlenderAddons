import bpy

from pathlib import Path
import json
import time

from . import uuids, hashing
from .utils import getRepoDir, getLocalDir

def getDatablocks(collection, include_nested=True):
    # Return a set of all datablocks that are children/dependencies
    # of a given collection (objects, object data, materials, 
    # textures, node trees, actions, nested collections, etc).
    result = {
        'objects':     set(),
        'meshes':      set(),
        'materials':   set(),
        'images':      set(),
        'node_groups': set(),
        'armatures':   set(),
        'actions':     set(),
    }

    def walk_node_tree(node_tree):
        if node_tree is None or node_tree in result['node_groups']:
            return
        result['node_groups'].add(node_tree)
        for node in node_tree.nodes:
            if hasattr(node, "node_tree") and node.node_tree:
                walk_node_tree(node.node_tree)  # nested groups
            if hasattr(node, "image") and node.image:
                result['images'].add(node.image)

    def walk_material(mat):
        if mat is None:
            return
        result['materials'].add(mat)
        if mat.node_tree:
            walk_node_tree(mat.node_tree)

    def walk_action(action):
        if action is not None:
            result['actions'].add(action)

    for obj in collection.all_objects:
        result['objects'].add(obj)

        # mesh data
        if obj.type == 'MESH' and obj.data:
            result['meshes'].add(obj.data)

        # armature data (the datablock, distinct from the Object)
        if obj.type == 'ARMATURE' and obj.data:
            result['armatures'].add(obj.data)

        # materials (+ their node trees/images)
        for slot in obj.material_slots:
            if slot.material:
                walk_material(slot.material)

        # object-level animation action
        if obj.animation_data:
            walk_action(obj.animation_data.action)

        # mesh/shape-key or other data-level animation (e.g. shape key actions)
        if obj.data and getattr(obj.data, "animation_data", None):
            walk_action(obj.data.animation_data.action)

        # geometry nodes modifiers reference node_groups directly
        for mod in obj.modifiers:
            if hasattr(mod, "node_group") and mod.node_group:
                walk_node_tree(mod.node_group)

    return result

def flattenDatablocks(datablocks):
    # Returns a set of datablocks for an input dict of categorized datablocks
    flat = set()
    for dbType,dbSet in datablocks.items():
        for db in dbSet:
            flat.add(db)
    return flat

def hashDatablocks(datablocks):
    assetData = {}
    for dbType,dbSet in datablocks.items():
        for db in dbSet:
            hashRes = hashing.hashBlock(db,dbType)
            assetData[db.uuid] = {"hash":hashRes,"name":db.name,"type":dbType}
            db.last_hash = hashRes
    return assetData

def diff(collection, assetData=None):
    # Diff against local commit file, optional assetData input to avoid recomputing it
    # Names and paths
    repoDir = getRepoDir()
    assetName = collection.name
    localDir = getLocalDir(assetName)
    commitDir = localDir / "commits"
    headFile = commitDir / "head"

    if not headFile.exists():
        print(f"VERSION CONTROL: ERROR, Local head file '{str(headFile)}' not found!")
        return

    header = int(headFile.read_text(encoding="utf-8").strip())
    assetManifest = commitDir / f"{header}.json"

    if not assetManifest.exists():
        print(f"VERSION CONTROL: ERROR, Local asset manifest '{str(assetManifest)}' not found!")
        return

    if assetData is None:
        datablocks = getDatablocks(collection)
        flatDatablocks = flattenDatablocks(datablocks)
        uuids.ensureUuids(flatDatablocks)
        assetData = hashDatablocks(datablocks)

    with open(str(assetManifest),"r",encoding="utf-8") as f:
        manifestData = json.load(f)["assetData"]

    added = set() 
    removed = set()
    modified = set()
    unchanged = set()

    for localUuid in assetData.keys():
        if localUuid not in manifestData.keys():
            added.add(assetData[localUuid]["name"])

    for manifestUuid, data in manifestData.items():
        manifestHash = data["hash"]
        manifestName = data["name"]

        if manifestUuid not in assetData.keys():
            removed.add(manifestName)
            continue
        else:
            # Check for diffs
            localHash = assetData[manifestUuid]["hash"]
            if localHash != manifestHash:
                modified.add(manifestName)
                continue
            else:
                # Unchanged
                unchanged.add(manifestName)
                continue

    results = {"added":added,"removed":removed,"modified":modified,"unchanged":unchanged}
    return results

def commit(collection,message=""):
    # Commit writes a local history
    # NOTE[Josh] TO avoid future conflicts a commit lock should be made with user ids
    assetName = collection.name
    localDir = getLocalDir(assetName)
    commitDir = localDir / "commits"
    commitDir.mkdir(parents=True,exist_ok=True)
    headFile = commitDir / "head"
    if headFile.exists():
        oldHeader = int(headFile.read_text(encoding="utf-8").strip())
        currentHeader = oldHeader + 1
    else:
        currentHeader = 1

    datablocks = getDatablocks(collection)
    flatDatablocks = flattenDatablocks(datablocks)
    uuids.ensureUuids(flatDatablocks)
    assetData = hashDatablocks(datablocks)

    diffResult = diff(collection, assetData=assetData)
    if not (diffResult["added"] or diffResult["removed"] or diffResult["modified"]):
        print("VERSION CONTROL: Nothing to commit, no changes since last commit")
        return None
    
    blendCommitFile = commitDir / f"{currentHeader}.blend"
    manifestCommitFile = commitDir / f"{currentHeader}.json"

    bpy.data.libraries.write(str(blendCommitFile), flatDatablocks, fake_user=True)
    with open(manifestCommitFile, "w", encoding="utf-8") as f:
        json.dump({"timestamp": time.time(),"message":message,"assetData": assetData}, f, indent=4)

    # Update header
    headFile.write_text(str(currentHeader), encoding="utf-8")
    return currentHeader

def push(collection):
    # Names and paths
    repoDir = getRepoDir()
    assetName = collection.name
    assetBlend = str(repoDir / f"{assetName}.blend")
    assetManifest = str(repoDir / f"{assetName}.json")

    # Datablocks
    datablocks = getDatablocks(collection)
    uuids.ensureUuids(datablocks)
    assetData = hashDatablocks(datablocks)

    # Save out to repo
    with open(assetManifest,"w",encoding="utf-8") as f:
        json.dump(assetData, f, indent=4)
    
    bpy.data.libraries.write(assetBlend,datablocks,fake_user=True)

def pull(collection,datablocks):
    # Names and paths
    repoDir = getRepoDir()
    assetName = collection.name
    assetBlend = repoDir / f"{assetName}.blend"
    assetManifest = repoDir / f"{assetName}.json"

    if not assetBlend.exists():
        print(f"VERSION CONTROL: ERROR, Asset '{assetBlend}' not found in repository!")
        return

    if not assetManifest.exists():
        print(f"VERSION CONTROL: ERROR, Asset Manifest '{assetManifest}' not found in repository!")
        return

