# Batch preview renderer for the Lego Environment addon.
# Run from Blender's Text Editor (not --background: viewport render needs a GPU context)
# with the addon enabled. Progress is printed to the system console.

import bpy
import os
import sys
import math
import time
import traceback
from mathutils import Vector, Euler, Color

# SETTINGS
RESOLUTION = 512
MARGIN = 1.15                   # padding around the brick in frame
SKIP_EXISTING = True            # don't re-render previews that already exist
LIMIT = None                    # e.g. 20 to test on the first 20 parts
JPEG_QUALITY = 90
CAM_ROTATION = Euler((math.radians(60), 0, math.radians(45)))  # 3/4 view looking down


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


def viewportRender(path):
    bpy.context.scene.render.filepath = path
    override = {"window": bpy.context.window}
    # give the operator a 3D view if one is open (harmless otherwise)
    for area in bpy.context.window.screen.areas:
        if area.type == 'VIEW_3D':
            override["area"] = area
            override["region"] = next(r for r in area.regions if r.type == 'WINDOW')
            break
    with bpy.context.temp_override(**override):
        # view_context=False: render through the scene camera with the scene's Workbench settings
        bpy.ops.render.opengl(write_still=True, view_context=False)


def main():
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

    window = bpy.context.window
    originalScene = window.scene
    scene, cam = makePreviewScene()
    window.scene = scene

    done, skipped, failed = 0, 0, []
    start = time.perf_counter()
    try:
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
                viewportRender(outPath)
                done += 1
            except Exception as e:
                traceback.print_exc()
                failed.append((brickId, str(e)))
            finally:
                if obj is not None:
                    mesh = obj.data
                    bpy.data.objects.remove(obj)
                    bpy.data.meshes.remove(mesh)

            print(f"[{n}/{len(ids)}] {brickId}")
    finally:
        window.scene = originalScene
        camData, world = cam.data, scene.world
        bpy.data.objects.remove(cam)
        bpy.data.cameras.remove(camData)
        bpy.data.scenes.remove(scene)
        bpy.data.worlds.remove(world)

    mins = (time.perf_counter() - start) / 60
    print(f"\nPreviews: {done} rendered, {skipped} skipped, {len(failed)} failed in {mins:.1f} min")
    for brickId, reason in failed:
        print(f"  FAILED {brickId}: {reason}")
    print(f"Saved to {outDir}")


main()