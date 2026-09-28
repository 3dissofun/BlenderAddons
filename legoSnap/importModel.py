# Imports whole LDraw models (.ldr / .mpd) such as car.ldr.
# Every part line is built with brickBuilder.makeBrick (so parts keep their lego_snaps),
# placed with its LDraw transform, and coloured from LDConfig.ldr in the piece library.
# register() / unregister() add and remove File > Import > LDraw Model (.ldr/.mpd)

import os
import time
import traceback

import bpy
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper
from mathutils import Matrix, Vector

from . import brickBuilder, datParser

MAX_SUBMODEL_DEPTH = 32
MODEL_EXTS = (".ldr", ".mpd")

# FILE HELPERS

def readLines(path):
    # older LDraw files are not always valid UTF-8
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()

def normKey(name):
    # LDraw references are case-insensitive and may use either slash
    return name.strip().lower().replace("\\", "/")

def stem(ref):
    return os.path.splitext(os.path.basename(ref.replace("\\", "/")))[0]

def splitSections(lines, fallbackName):
    # splits an .mpd into {sectionKey: lines}; a plain .ldr becomes a single section
    # returns (sections, mainKey) where mainKey is the first FILE in the document
    sections, mainKey, current = {}, None, None
    for line in lines:
        parts = line.strip().split(None, 2)
        if parts[:2] == ["0", "FILE"]:
            current = normKey(parts[2] if len(parts) > 2 else "")
            sections[current] = []
            mainKey = mainKey or current
            continue
        if parts[:2] in (["0", "NOFILE"], ["0", "!DATA"]):
            current = None  # embedded data blocks are not model geometry
            continue
        if current is not None:
            sections[current].append(line)

    if mainKey is None:
        mainKey = normKey(fallbackName)
        sections = {mainKey: lines}
    return sections, mainKey

def toBlender(ldMatrix):
    # LDraw 4x4 (numpy, LDU) -> Blender 4x4, matching the vertex conversion in brickBuilder
    # the conversion is a homomorphism, so nested transforms can be converted per level
    P = brickBuilder.LDtoBL
    rot = P @ Matrix(ldMatrix[:3, :3].tolist()) @ P.transposed()
    out = rot.to_4x4()
    out.translation = (P @ Vector(ldMatrix[:3, 3].tolist())) * brickBuilder.brickSf
    return out

# COLOURS

def srgbToLinear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def hexToLinear(value):
    v = value.strip().lstrip("#")
    if v.lower().startswith("0x"):
        v = v[2:]
    v = v[-6:].rjust(6, "0")
    return tuple(srgbToLinear(int(v[k:k + 2], 16) / 255) for k in (0, 2, 4))

def loadColourTable(libraryDir):
    # {code: (name, linearRgb, alpha, finish)} from LDConfig.ldr
    table = {}
    path = None
    for name in ("LDConfig.ldr", "ldconfig.ldr", "LDCONFIG.LDR"):
        candidate = os.path.join(libraryDir, name)
        if os.path.isfile(candidate):
            path = candidate
            break
    if not path:
        print("LDRAW IMPORT: WARNING, LDConfig.ldr not found in piece library, using fallback colours")
        return table

    for line in readLines(path):
        parts = line.split()
        if parts[:2] != ["0", "!COLOUR"] or len(parts) < 3:
            continue
        upper = [p.upper() for p in parts]

        def after(key):
            if key in upper:
                i = upper.index(key)
                if i + 1 < len(parts):
                    return parts[i + 1]
            return None

        code, value = after("CODE"), after("VALUE")
        if code is None or value is None:
            continue
        try:
            alpha = int(after("ALPHA") or 255) / 255
            rgb = hexToLinear(value)
            code = int(code)
        except ValueError:
            continue
        if any(k in upper for k in ("CHROME", "METAL", "PEARLESCENT")):
            finish = "METAL"
        elif "RUBBER" in upper:
            finish = "RUBBER"
        else:
            finish = ""
        table[code] = (parts[2], rgb, alpha, finish)
    return table

# IMPORTER

class ModelImporter:
    def __init__(self, collection, shareMeshes, useColours, keepSubmodels):
        self.collection = collection
        self.shareMeshes = shareMeshes
        self.useColours = useColours
        self.keepSubmodels = keepSubmodels

        self.colours = loadColourTable(datParser.libraryDir) if useColours else {}
        self.meshCache = {}   # part key -> mesh (None when the part failed to build)
        self.fileCache = {}   # external .ldr path -> (sections, mainKey) or None
        self.active = set()   # submodels currently being built, guards against cycles
        self.missing = set()
        self.topLevel = []
        self.partCount = 0

    # objects

    def newObject(self, name, data, parent, matrix):
        obj = bpy.data.objects.new(name, data)
        if data is None:
            obj.empty_display_type = 'PLAIN_AXES'
            obj.empty_display_size = 0.2
        self.collection.objects.link(obj)
        obj.parent = parent
        obj.matrix_basis = matrix  # parent inverse is identity, so this is relative to the parent
        if parent is None:
            self.topLevel.append(obj)
        return obj

    def getMesh(self, ref):
        key = normKey(ref)
        if key in self.meshCache:
            mesh = self.meshCache[key]
            if mesh is None or self.shareMeshes:
                return mesh
            return mesh.copy()  # copies lego_snaps with it

        brickId = ref[:-4] if ref.lower().endswith(".dat") else ref
        try:
            obj = brickBuilder.makeBrick(brickId)
        except Exception:
            traceback.print_exc()
            obj = None
        if obj is None:
            self.missing.add(ref)
            self.meshCache[key] = None
            return None

        mesh = obj.data
        bpy.data.objects.remove(obj)  # only the mesh is kept; each placement gets its own object
        if self.useColours and not len(mesh.materials):
            mesh.materials.append(None)  # slot for the per-object colour material
        self.meshCache[key] = mesh
        return mesh

    # colours

    def colourInfo(self, code):
        if code in self.colours:
            return self.colours[code]
        if 0x2000000 <= code <= 0x2FFFFFF:  # LDraw direct colour 0x2RRGGBB
            return (f"Direct_{code & 0xFFFFFF:06X}", hexToLinear(f"{code & 0xFFFFFF:06X}"), 1.0, "")
        return (f"Unknown_{code}", (0.5, 0.5, 0.5), 1.0, "")

    def getMaterial(self, code):
        name, rgb, alpha, finish = self.colourInfo(code)
        matName = f"LDraw {code} {name}"
        mat = bpy.data.materials.get(matName)
        if mat:
            return mat

        rgba = (*rgb, alpha)
        mat = bpy.data.materials.new(matName)
        mat.diffuse_color = rgba
        metallic = 1.0 if finish == "METAL" else 0.0
        roughness = 0.25 if finish == "METAL" else 0.8 if finish == "RUBBER" else 0.3
        mat.metallic = metallic
        mat.roughness = roughness

        tree = mat.node_tree
        if tree is None and hasattr(mat, "use_nodes"):
            mat.use_nodes = True
            tree = mat.node_tree
        if tree:
            bsdf = next((n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if bsdf:
                for socket, value in (("Base Color", rgba), ("Alpha", alpha),
                                      ("Metallic", metallic), ("Roughness", roughness)):
                    if socket in bsdf.inputs:
                        bsdf.inputs[socket].default_value = value
        return mat

    def applyColour(self, obj, code):
        mat = self.getMaterial(code)
        slot = obj.material_slots[0]
        slot.link = 'OBJECT'  # meshes are shared, so colour lives on the object
        slot.material = mat
        obj.color = mat.diffuse_color  # for Solid view with Object colour

    # model tree

    def findSubmodel(self, sections, refKey, ref, modelDir):
        # returns (sections, key, dir) for a submodel, False for a missing model file, None for a part
        if refKey in sections:
            return sections, refKey, modelDir
        if os.path.splitext(refKey)[1] not in MODEL_EXTS:
            return None
        path = os.path.join(modelDir, ref.replace("\\", os.sep))
        if path not in self.fileCache:
            self.fileCache[path] = splitSections(readLines(path), ref) if os.path.isfile(path) else None
        loaded = self.fileCache[path]
        if not loaded:
            self.missing.add(ref)
            return False
        subSections, mainKey = loaded
        return subSections, mainKey, os.path.dirname(path)

    def build(self, sections, key, modelDir, parent, accum, colour, depth=0):
        guard = (id(sections), key)
        self.active.add(guard)

        for line in sections.get(key, []):
            parts = line.split()
            if not parts or parts[0] != "1":
                continue
            try:
                partColour, ldMatrix, ref = datParser.parseInclude(line.strip()[1:])
            except (ValueError, IndexError):
                print(f"LDRAW IMPORT: WARNING, bad line skipped: {line.strip()}")
                continue
            if partColour == 16:
                partColour = colour
            matrix = accum @ toBlender(ldMatrix)

            sub = self.findSubmodel(sections, normKey(ref), ref, modelDir)
            if sub is False:
                continue
            if sub:
                subSections, subKey, subDir = sub
                if (id(subSections), subKey) in self.active or depth >= MAX_SUBMODEL_DEPTH:
                    # checked here so no empty is left behind for a skipped submodel
                    print(f"LDRAW IMPORT: WARNING, submodel '{ref}' is recursive or nested too deep, skipped")
                    continue
                if self.keepSubmodels:
                    empty = self.newObject(stem(ref), None, parent, matrix)
                    self.build(subSections, subKey, subDir, empty, Matrix.Identity(4), partColour, depth + 1)
                else:
                    self.build(subSections, subKey, subDir, None, matrix, partColour, depth + 1)
                continue

            mesh = self.getMesh(ref)
            if mesh is None:
                continue
            obj = self.newObject(stem(ref), mesh, parent, matrix)
            if self.useColours:
                self.applyColour(obj, partColour)
            self.partCount += 1

        self.active.discard(guard)

# OPERATOR

class LE_OT_ImportModel(bpy.types.Operator, ImportHelper):
    bl_idname = "lego.import_model"
    bl_label = "Import LDraw Model"
    bl_description = "Import an LDraw model (.ldr / .mpd) as snappable bricks"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".ldr"
    filter_glob: StringProperty(default="*.ldr;*.mpd", options={'HIDDEN'})

    share_meshes: BoolProperty(
        name="Share Meshes",
        description="Repeated parts use one mesh (much faster, smaller files)",
        default=True,
    )
    use_colours: BoolProperty(
        name="Colours",
        description="Create materials from LDConfig.ldr colours",
        default=True,
    )
    keep_submodels: BoolProperty(
        name="Keep Submodels",
        description="Parent submodel parts under empties; off places every part at top level",
        default=True,
    )

    def execute(self, context):
        start = time.perf_counter()
        prefs = context.preferences.addons[__package__].preferences
        datParser.libraryDir = prefs.pieceLibrary
        datParser.shadowDir = prefs.shadowLibrary
        if not os.path.isdir(datParser.libraryDir):
            self.report({'ERROR'}, "Piece library folder not found, check the add-on preferences")
            return {'CANCELLED'}

        path = self.filepath
        if not os.path.isfile(path):
            self.report({'ERROR'}, f"File not found: {path}")
            return {'CANCELLED'}

        sections, mainKey = splitSections(readLines(path), os.path.basename(path))
        collection = bpy.data.collections.new(stem(path))
        context.scene.collection.children.link(collection)

        importer = ModelImporter(collection, self.share_meshes, self.use_colours, self.keep_submodels)
        importer.build(sections, mainKey, os.path.dirname(path), None, Matrix.Identity(4), 16)

        if not importer.partCount:
            bpy.data.collections.remove(collection)
            self.report({'WARNING'}, "No parts could be imported from this file")
            return {'CANCELLED'}

        for o in context.selected_objects:
            o.select_set(False)
        for o in importer.topLevel:
            o.select_set(True)
        context.view_layer.objects.active = importer.topLevel[0]

        duration = time.perf_counter() - start
        unique = sum(1 for m in importer.meshCache.values() if m)
        self.report({'INFO'}, f"Imported {importer.partCount} parts ({unique} unique) in {duration:.2f}s")
        if importer.missing:
            names = ", ".join(sorted(importer.missing))
            self.report({'WARNING'}, f"{len(importer.missing)} missing: {names}")
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "share_meshes")
        layout.prop(self, "use_colours")
        layout.prop(self, "keep_submodels")

class LE_FH_LDrawModel(bpy.types.FileHandler):
    # drag and drop .ldr / .mpd files onto the 3D viewport
    bl_idname = "LE_FH_ldraw_model"
    bl_label = "LDraw Model"
    bl_import_operator = LE_OT_ImportModel.bl_idname
    bl_file_extensions = ".ldr;.mpd"

    @classmethod
    def poll_drop(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

def menuImport(self, context):
    self.layout.operator(LE_OT_ImportModel.bl_idname, text="LDraw Model (.ldr/.mpd)")

classes = (LE_OT_ImportModel, LE_FH_LDrawModel)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menuImport)

def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menuImport)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
