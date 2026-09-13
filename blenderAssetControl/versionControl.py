import bpy

from pathlib import Path
import json

from . import uuids, hashing

repoDir = Path(r"C:\Users\User\Desktop\BlenderScriptLibrary\addons\blenderAssetControl\repo")

def getDatablocks(collection, include_nested=True):
    # Return a set of all datablocks that are children/dependencies
    # of a given collection (objects, object data, materials, 
    # textures, node trees, actions, nested collections, etc).
    found = set()

    def add(db):
        if db is not None and db not in found:
            found.add(db)
            return True
        return False

    def walk_material(mat):
        if not add(mat):
            return
        if mat.node_tree:
            walk_node_tree(mat.node_tree)
        if mat.animation_data and mat.animation_data.action:
            add(mat.animation_data.action)

    def walk_node_tree(node_tree):
        if not add(node_tree):
            return
        for node in node_tree.nodes:
            # Image textures
            if hasattr(node, "image") and node.image:
                add(node.image)
            # Nested node groups
            if node.type == 'GROUP' and node.node_tree:
                walk_node_tree(node.node_tree)

    def walk_object(obj):
        if not add(obj):
            return

        # Object data (mesh, curve, armature, light, camera, etc.)
        if obj.data:
            add(obj.data)

        # Materials on the object itself and on its data
        for slot in obj.material_slots:
            if slot.material:
                walk_material(slot.material)

        # Mesh-linked materials (in case slots miss any)
        if obj.type == 'MESH' and obj.data:
            for mat in obj.data.materials:
                if mat:
                    walk_material(mat)

        # Armature data
        if obj.type == 'ARMATURE' and obj.data:
            add(obj.data)

        # Modifiers referencing other datablocks
        for mod in obj.modifiers:
            for attr in ("object", "object_from", "object_to", "target",
                         "node_group", "collection"):
                sub = getattr(mod, attr, None)
                if sub is None:
                    continue
                if isinstance(sub, bpy.types.Object):
                    walk_object(sub)
                elif isinstance(sub, bpy.types.Collection):
                    walk_collection(sub)
                elif isinstance(sub, bpy.types.NodeTree):
                    walk_node_tree(sub)
                else:
                    add(sub)

        # Particle systems -> instance objects/collections
        for psys in obj.particle_systems:
            settings = psys.settings
            if settings:
                add(settings)
                if settings.instance_object:
                    walk_object(settings.instance_object)
                if settings.instance_collection:
                    walk_collection(settings.instance_collection)

        # Animation
        if obj.animation_data and obj.animation_data.action:
            add(obj.animation_data.action)

        # Object-level custom node groups (geometry nodes etc. covered by modifiers above)

    def walk_collection(coll):
        if not add(coll):
            return
        for obj in coll.objects:
            walk_object(obj)
        if include_nested:
            for child in coll.children:
                walk_collection(child)

    walk_collection(collection)
    return found

def hashDatablocks(datablocks):
    assetData = {}
    for db in datablocks:
        hashRes = hashing.hashBlock(db)
        assetData[db.uuid] = {"hash":hashRes,"name":db.name,"type":type(db).__name__}
        db.last_hash = hashRes
    return assetData

def push(collection):
    # Names and paths
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
    assetName = collection.name
    assetBlend = repoDir / f"{assetName}.blend"
    assetManifest = repoDir / f"{assetName}.json"

    if not assetBlend.exists():
        print(f"VERSION CONTROL: ERROR, Asset '{assetBlend}' not found in repository!")
        return

    if not assetManifest.exists():
        print(f"VERSION CONTROL: ERROR, Asset Manifest '{assetManifest}' not found in repository!")
        return

def diff(collection):
    # Names and paths
    assetName = collection.name
    assetBlend = repoDir / f"{assetName}.blend"
    assetManifest = repoDir / f"{assetName}.json"

    if not assetBlend.exists():
        print(f"VERSION CONTROL: ERROR, Asset '{assetBlend}' not found in repository!")
        return

    if not assetManifest.exists():
        print(f"VERSION CONTROL: ERROR, Asset Manifest '{assetManifest}' not found in repository!")
        return

    assetBlend = str(assetBlend)
    assetManifest = str(assetManifest)

    datablocks = getDatablocks(collection)
    uuids.ensureUuids(datablocks)
    assetData = hashDatablocks(datablocks)

    with open(assetManifest,"r",encoding="utf-8") as f:
        manifestData = json.load(f)

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
