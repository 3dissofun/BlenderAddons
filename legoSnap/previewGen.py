# Batch preview renderer for the Lego Environment addon - parallel version.
#
# Blender's Python API isn't thread-safe and rendering has to happen on the main thread,
# so instead of threads this runs several background Blender processes ("workers"),
# each rendering its own share of the parts list. The Workbench engine renders fine in
# --background mode, so the workers don't need a window.
#
# Run it either way:
#   - from Blender's Text Editor with the addon enabled (Blender waits until all workers finish), or
#   - from a terminal:  blender --background --python render_previews.py
# Progress from all workers is printed to the system console / terminal.

import bpy
import os
import sys
import json
import math
import time
import tempfile
import traceback
import subprocess
from mathutils import Vector, Euler, Color

# SETTINGS
WORKERS = min(4, os.cpu_count() or 1)  # parallel Blender processes; they share one GPU, so more isn't always faster
RESOLUTION = 512
MARGIN = 1.15                   # padding around the brick in frame
SKIP_EXISTING = True            # don't re-render previews that already exist
LIMIT = None                    # e.g. 20 to test on the first 20 parts
JPEG_QUALITY = 90
RENDER_AA = '8'                 # Workbench anti-aliasing samples: 'OFF', 'FXAA', '5', '8', '11', '16', '32'
CAM_ROTATION = Euler((math.radians(60), 0, math.radians(45)))  # 3/4 view looking down


# ---------------------------------------------------------------- shared helpers

def themeBackground():
    # the 3D viewport's theme background, converted to linear so the render matches on screen
    theme = bpy.context.preferences.themes[0].view_3d.space.gradients
    col = Color(theme.high_gradient[:3])
    return col.from_srgb_to_scene_linear()


def findAddon():
    # the addon package name depends on how it was installed, so look it up by module
    for name, mod in list(sys.modules.items()):
        if name.endswith(".brickBuilder") and hasattr(mod, "makeBrick"):
            pkg = mod.__package__
            return mod, sys.modules[pkg + ".datParser"], pkg
    raise RuntimeError("Lego Environment addon is not loaded - enable it in Preferences first")


def makePreviewScene():
    scene = bpy.data.scenes.new("LegoPreviews")

    camData = bpy.data.cameras.new("PreviewCam")
    camData.type = 'ORTHO'
    cam = bpy.data.objects.new("PreviewCam", camData)
    cam.rotation_euler = CAM_ROTATION
    scene.collection.objects.link(cam)
    scene.camera = cam

    r = scene.render
    r.engine = 'BLENDER_WORKBENCH'
    r.resolution_x = r.resolution_y = RESOLUTION
    r.resolution_percentage = 100
    r.film_transparent = False
    r.image_settings.file_format = 'JPEG'
    r.image_settings.color_mode = 'RGB'
    r.image_settings.quality = JPEG_QUALITY
    scene.view_settings.view_transform = 'Standard'
    scene.display.render_aa = RENDER_AA

    shading = scene.display.shading
    shading.light = 'STUDIO'
    shading.color_type = 'MATERIAL'  # default solid view: no material -> standard grey
    shading.show_cavity = True
    shading.cavity_type = 'WORLD'
    shading.show_object_outline = False

    # Workbench renders take their background from the world colour
    world = bpy.data.worlds.new("PreviewWorld")
    world.color = themeBackground()
    scene.world = world
    return scene, cam


def frameCamera(cam, obj):
    # fit an orthographic camera (at a fixed angle) to the object's world bounding box
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    centre = sum(corners, Vector()) / len(corners)

    rot = cam.rotation_euler.to_matrix()
    right, up, forward = rot.col[0], rot.col[1], -rot.col[2]

    def extent(axis):
        vals = [(c - centre).dot(axis) for c in corners]
        return max(vals) - min(vals)

    size = max(extent(right), extent(up), 1e-4)  # square render, so the larger side wins
    depth = extent(forward)

    dist = depth + 1.0
    cam.location = centre - forward * dist
    cam.data.ortho_scale = size * MARGIN
    cam.data.clip_start = 0.001
    cam.data.clip_end = dist + depth + 1.0


# ---------------------------------------------------------------- worker (background Blender)

def worker(shard, shardCount, resultPath):
    brickBuilder, datParser, pkg = findAddon()
    prefs = bpy.context.preferences.addons[pkg].preferences
    datParser.libraryDir = bpy.path.abspath(prefs.pieceLibrary)
    datParser.shadowDir = bpy.path.abspath(prefs.shadowLibrary)

    partsDir = os.path.join(datParser.libraryDir, "parts")
    outDir = os.path.join(datParser.libraryDir, "previews")
    os.makedirs(outDir, exist_ok=True)

    # top-level .dat files only (the s/ subparts folder is skipped)
    ids = sorted(os.path.splitext(f)[0] for f in os.listdir(partsDir)
                 if f.lower().endswith(".dat") and os.path.isfile(os.path.join(partsDir, f)))
    if LIMIT:
        ids = ids[:LIMIT]
    # interleaved split, so simple and complex parts are spread evenly across workers
    ids = ids[shard::shardCount]

    scene, cam = makePreviewScene()
    done, skipped, failed = 0, 0, []

    for n, brickId in enumerate(ids, 1):
        outPath = os.path.join(outDir, f"{brickId}.jpg")
        if SKIP_EXISTING and os.path.isfile(outPath):
            skipped += 1
            continue

        obj = None
        try:
            obj = brickBuilder.makeBrick(brickId)
            if obj is None:
                failed.append((brickId, "no geometry"))
                continue
            scene.collection.objects.link(obj)
            frameCamera(cam, obj)
            scene.render.filepath = outPath
            bpy.ops.render.render(write_still=True, scene=scene.name)
            done += 1
        except Exception as e:
            traceback.print_exc()
            failed.append((brickId, str(e)))
        finally:
            if obj is not None:
                mesh = obj.data
                bpy.data.objects.remove(obj)
                bpy.data.meshes.remove(mesh)

        print(f"[worker {shard + 1}] [{n}/{len(ids)}] {brickId}", flush=True)

    with open(resultPath, "w") as f:
        json.dump({"done": done, "skipped": skipped, "failed": failed, "outDir": outDir}, f)


# ---------------------------------------------------------------- launcher

def workerScript(tmpDir):
    # Workers need this script as a file on disk. When run from the Text Editor, use the
    # text block's current contents (works even if it's unsaved); otherwise use __file__.
    space = getattr(bpy.context, "space_data", None)
    if space is not None and space.type == 'TEXT_EDITOR' and space.text is not None:
        path = os.path.join(tmpDir, "render_previews_worker.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(space.text.as_string())
        return path
    if os.path.isfile(__file__):
        return __file__
    raise RuntimeError("Can't locate this script on disk - save it to a .py file and run it again")


def launcher():
    findAddon()  # fail early if the addon isn't enabled
    tmpDir = tempfile.mkdtemp(prefix="lego_previews_")
    script = workerScript(tmpDir)

    print(f"Starting {WORKERS} render workers...", flush=True)
    start = time.perf_counter()
    procs = []
    try:
        for i in range(WORKERS):
            resultPath = os.path.join(tmpDir, f"worker{i}.json")
            cmd = [bpy.app.binary_path, "--background", "--python", script,
                   "--", "--shard", str(i), str(WORKERS), resultPath]
            procs.append((i, resultPath, subprocess.Popen(cmd)))
        for _, _, p in procs:
            p.wait()
    finally:
        # if the launcher is interrupted, don't leave workers running
        for _, _, p in procs:
            if p.poll() is None:
                p.terminate()

    done, skipped, failed, outDir = 0, 0, [], None
    for i, resultPath, p in procs:
        if not os.path.isfile(resultPath):
            failed.append((f"worker {i + 1}", f"crashed (exit code {p.returncode}) - see console output"))
            continue
        with open(resultPath) as f:
            res = json.load(f)
        done += res["done"]
        skipped += res["skipped"]
        failed += [tuple(x) for x in res["failed"]]
        outDir = res["outDir"]

    mins = (time.perf_counter() - start) / 60
    print(f"\nPreviews: {done} rendered, {skipped} skipped, {len(failed)} failed in {mins:.1f} min")
    for brickId, reason in failed:
        print(f"  FAILED {brickId}: {reason}")
    if outDir:
        print(f"Saved to {outDir}")


# ---------------------------------------------------------------- entry point

def shardArgs():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "--shard" in argv:
        i = argv.index("--shard")
        return int(argv[i + 1]), int(argv[i + 2]), argv[i + 3]
    return None


args = shardArgs()
if args:
    worker(*args)
else:
    launcher()
