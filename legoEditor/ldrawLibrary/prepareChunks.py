"""
Prepare the library chunk files for the point cloud importer.

For every mesh in every ldrawLibrary_*.blend:
  * makes sure an object with the same name exists ("04315_3001"), creating it
    if the chunk only has the mesh
  * sets on the object:
        lego_piece     INT, piece id (from the name prefix)
        lego_ref       STR, part name from pieceIndex.bin ("3001")
        lego_embedded  False
        is_lego        False
        location       (id, 0, 0), rotation and scale reset
  * keeps every object in a "LEGO_BrickLibrary" collection in the scene, so
    nothing in the file is left without a user
  * saves the file uncompressed

From inside Blender: F3 > "Prepare Library Chunks" (libraryOps.py), which runs
this script in a separate background Blender so your open file is untouched.

Or from a command prompt (the add-on doesn't need to be enabled):

    blender -b --factory-startup --python prepareChunks.py -- "C:/path/to/ldrawLibrary" "C:/path/to/ldrawIndex.bin"

Both arguments are optional; they default to the folders next to this script.
Back up the chunk files first: they are overwritten in place.
"""
from pathlib import Path
import struct
import sys

import bpy

HERE = Path(__file__).resolve().parent
EXTERNAL = HERE.parent / "external"
RECORD = 64
COLL_NAME = "LEGO_BrickLibrary"

def readIndex(path):
    data = Path(path).read_bytes()
    names = []
    for off in range(0, len(data), RECORD):
        _, n = struct.unpack_from("<II", data, off)
        names.append(data[off + 8: off + 8 + n].decode())
    return names

def prepare(path, names):
    bpy.ops.wm.open_mainfile(filepath=str(path))
    scene = bpy.context.scene
    coll = bpy.data.collections.get(COLL_NAME)
    if coll is None:
        coll = bpy.data.collections.new(COLL_NAME)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)

    made = updated = skipped = 0
    for mesh in list(bpy.data.meshes):
        if mesh.library is not None:
            continue                               # linked in from elsewhere
        prefix = mesh.name.split("_", 1)[0]
        if not prefix.isdigit():
            print(f"  skipping mesh '{mesh.name}': no id prefix")
            skipped += 1
            continue
        pid = int(prefix)
        if pid >= len(names):
            print(f"  skipping mesh '{mesh.name}': id {pid} not in the index")
            skipped += 1
            continue

        obj = bpy.data.objects.get(mesh.name)
        if obj is None or obj.data != mesh:
            obj = bpy.data.objects.new(mesh.name, mesh)
            made += 1
        else:
            updated += 1
        if obj.name != mesh.name:                  # a different object had the name
            print(f"  WARNING, object for '{mesh.name}' is called '{obj.name}'")

        obj["lego_piece"] = pid
        obj["lego_ref"] = names[pid]
        obj["lego_embedded"] = False
        obj["is_lego"] = False
        obj.parent = None
        obj.location = (float(pid), 0.0, 0.0)
        obj.rotation_mode = 'XYZ'
        obj.rotation_euler = (0.0, 0.0, 0.0)
        obj.scale = (1.0, 1.0, 1.0)
        if coll not in obj.users_collection:
            coll.objects.link(obj)
        mesh.use_fake_user = True                  # appendMeshes still works too

    bpy.ops.wm.save_mainfile(filepath=str(path), compress=False)
    print(f"{path.name}: {made} objects made, {updated} updated, {skipped} skipped")

def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    libDir = Path(argv[0]) if len(argv) > 0 else EXTERNAL / "ldrawLibrary"
    indexPath = Path(argv[1]) if len(argv) > 1 else EXTERNAL / "ldrawIndex.bin"

    names = readIndex(indexPath)
    chunks = sorted(libDir.glob("ldrawLibrary_*.blend"))
    if not chunks:
        print(f"No chunk files in '{libDir}'")
        return
    print(f"PREPARE: {len(chunks)} chunk files", flush=True)
    for i, path in enumerate(chunks, 1):
        prepare(path, names)
        print(f"PREPARE: done {i}/{len(chunks)}", flush=True)

if __name__ == "__main__":   # run by blender --python, not when imported
    main()
