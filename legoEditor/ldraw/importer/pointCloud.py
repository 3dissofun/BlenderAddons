"""
Import an LDR/MPD as point clouds instead of one object per brick.

Layout of an import called "<name>":

    <name>                      collection, linked to the scene
    ├─ main                     point cloud object, the main model's own bricks
    │  ├─ wheel                 sub-model placements: objects sharing the
    │  └─ wheel.001             sub-model's point cloud, parented to the model
    │                           that places them, transformed by the placement
    └─ <name>_unused            sub-models in the file that nothing places
                                (at the origin, excluded from the view layer)

    LEGO_Bricks                 ONE collection of brick prototypes for the whole
    ├─ 3001.dat                 .blend, shared by every import (linked under the
    └─ 3641.dat                 scene collection, excluded from the view layer)

Prototypes keep their part name. Each one carries its piece id (from
pieceIndex.bin) as obj["lego_piece"] and sits at location (id, 0, 0): the node
group reads the ids back from those positions, so points only store the piece
id and nothing depends on the order of the collection. Don't move prototypes.
Parts with no index id (not in the index, or embedded in an MPD) get ids from
BUILT_BASE upwards.

Every model in the file has its own point cloud, holding only that model's
own bricks. A sub-model placed inside another model is an object that uses
the sub-model's point cloud, so a model placed many times shares one point
cloud. If a sub-model inherits its colour (code 16) and is placed in several
colours, it gets one point cloud per colour, e.g. "wheel [4]" and "wheel [1]".

Point attributes (POINT domain, Blender space, LDU):
    position     the brick's origin
    rotation     QUATERNION, brick rotation (mirroring folded into scale)
    scale        FLOAT_VECTOR, per axis scale, negative x for mirrored bricks
    lego_piece   INT, piece id from pieceIndex.bin (or BUILT_BASE+ for built parts)
    col          FLOAT_COLOR, scene linear RGBA (what obj.color holds for a brick)
    lego_code    INT, LDraw colour code

The geometry nodes group (LEGO_PointCloud, one for the whole file) on every point cloud
instances the right brick onto each point, then stores on the INSTANCE domain:
    col          FLOAT_COLOR, the brick colour
    colExists    BOOLEAN, always True
    lego_code    INT, the LDraw colour code
Read them in a shader with an Attribute node set to "Instancer". The group's
"Realize" input realizes the instances instead.

Bricks, in order of preference:
    1. the prebuilt brick library (ldrawLibrary chunk files): its objects,
       already prepared as prototypes by prepareChunks.py, linked for every
       part in the file in ONE index.appendObjects call before anything is built
    2. an existing brick mesh in the file (by part name, "3001.dat" or "3001")
    3. built from the .dat
A part that already has a prototype in LEGO_Bricks (from this import or an
earlier one) reuses it. Parts embedded in the MPD always skip all of that and
are built fresh for each import, so a file's own version never gets mixed up
with the library part. Textured meshes already in the file are rebuilt too;
textured library meshes are reused.
"""
import bpy
import numpy as np
from mathutils import Matrix
from pathlib import Path
import tempfile
import time

from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from . import importer, library
from .col import colors
from ..ldrawLibrary import index

LD_TO_BL = importer.LD_TO_BL
BL_FROM_LD = np.linalg.inv(LD_TO_BL)

BRICK_COLL = "LEGO_Bricks"   # shared prototype collection
NODE_GROUP = "LEGO_PointCloud"
BUILT_BASE = 100000          # first id for parts with no pieceIndex id
FALLBACK_RGBA = (0.5, 0.5, 0.5, 1.0)

ATTR_PIECE = "lego_piece"
ATTR_COLOR = "col"
ATTR_COL_EXISTS = "colExists"
ATTR_CODE = "lego_code"
ATTR_ROT = "rotation"
ATTR_SCALE = "scale"

# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

def _decompose(m3):
    # 3x3 Blender space matrix -> (unit quaternion wxyz, scale xyz).
    # Mirroring is folded into a negative x scale. Shear is discarded.
    m3 = np.array(m3, dtype=np.float64)
    sc = np.linalg.norm(m3, axis=0)
    zero = sc < 1e-9
    if zero.sum() >= 2:
        return (1.0, 0.0, 0.0, 0.0), tuple(sc)
    if zero.any():
        # A flattened axis (scale 0): rebuild it from the other two
        i = int(np.argmax(zero))
        a, b = m3[:, (i + 1) % 3], m3[:, (i + 2) % 3]
        m3[:, i] = np.cross(a, b)
        sc[i] = np.linalg.norm(m3[:, i])
    if np.linalg.det(m3) < 0:
        sc[0] = -sc[0]
    rot = m3 / sc
    if zero.any():
        sc[zero] = 0.0
    u, _, vt = np.linalg.svd(rot)       # clean up any shear / rounding
    rot = u @ vt
    q = Matrix(rot.tolist()).to_quaternion()
    return (q.w, q.x, q.y, q.z), tuple(sc)

def _toBlender(m):
    # 4x4 LDraw placement -> 4x4 Blender space matrix
    return LD_TO_BL @ m @ BL_FROM_LD

# ---------------------------------------------------------------------------
# Model file structure
# ---------------------------------------------------------------------------

def _directPlacements(lines):
    # (ref, colour code, 4x4 LDraw matrix) for each type 1 line, not expanded
    for line in lines:
        t = line.split()
        if len(t) < 15 or t[0] != "1":
            continue
        col = colors.parseCol(t[1])
        x, y, z, a, b, c, d, e, f, g, h, i = map(float, t[2:14])
        m = np.array([[a, b, c, x], [d, e, f, y], [g, h, i, z], [0, 0, 0, 1]])
        ref = " ".join(t[14:]).lower().replace("\\", "/")
        yield ref, col, m

def _excludeFromViewLayers(coll):
    # Bricks still evaluate (Collection Info pulls them in), they just aren't drawn
    def find(layerColl):
        if layerColl.collection == coll:
            return layerColl
        for child in layerColl.children:
            hit = find(child)
            if hit:
                return hit
        return None
    for scene in bpy.data.scenes:
        for vl in scene.view_layers:
            lc = find(vl.layer_collection)
            if lc:
                lc.exclude = True

class _Import:
    # State for one file import
    def __init__(self, collection):
        self.name = collection.name
        self.collection = collection
        self.models = {}          # every model in the file, {name: lines}
        self.embedded = set()     # parts defined inside the MPD
        self.brickIds = {}        # {part ref: piece id} resolved in this import
        self.modelData = {}       # {(model, inherited colour or None): PointCloud}
        self.modelObjs = []       # every point cloud object made
        self.placed = set()       # models placed at least once
        self.inherits = {}        # {model: uses colour 16}
        self.rgba = {}            # {colour code: rgba}
        self.libObjects = {}      # {part ref: prototype object} from the brick library
        self.stats = {"shared": 0, "library": 0, "reused": 0, "built": 0, "points": 0}
        self.newProtos = 0

        self.brickColl = _getBrickCollection()
        # Prototypes left by earlier imports, by piece id and by part ref.
        # Parts embedded in an MPD are never shared, so they aren't listed.
        self.protoById, self.protoByRef = {}, {}
        self.nextBuiltId = BUILT_BASE
        for o in self.brickColl.objects:
            pid = o.get(ATTR_PIECE)
            if pid is None:
                continue
            self.nextBuiltId = max(self.nextBuiltId, pid + 1)
            if not o.get("lego_embedded"):
                self.protoById[pid] = o
                self.protoByRef.setdefault(o.get("lego_ref", ""), o)
        self.nodeGroup = None

    # -- colours ------------------------------------------------------------

    def rgbaFor(self, code):
        if code not in self.rgba:
            self.rgba[code] = colors.rgbaFromCode(code) or FALLBACK_RGBA
        return self.rgba[code]

    def usesInherited(self, model, stack=()):
        # True if anything in this model ends up in colour 16 (the parent's colour)
        if model in self.inherits:
            return self.inherits[model]
        result = False
        for ref, col, _ in _directPlacements(self.models[model]):
            if col != 16:
                continue
            if ref in self.models:
                if ref not in stack and self.usesInherited(ref, stack + (model,)):
                    result = True
                    break
            else:
                result = True
                break
        self.inherits[model] = result
        return result

    # -- bricks -------------------------------------------------------------

    def isSpecial(self, ref, mesh):
        # Parts that are always built fresh instead of reusing a mesh in the file
        if ref in self.embedded:
            return True
        if mesh is not None and "tex" in mesh.attributes:
            return True
        return False

    def findMesh(self, ref):
        mesh = bpy.data.meshes.get(ref)
        if mesh is None and ref.endswith(".dat"):
            mesh = bpy.data.meshes.get(ref[:-4])   # single brick imports drop .dat
        return mesh

    def libraryRefs(self):
        # Every library part placed by any model in the file that has no
        # prototype yet (not sub-models, not parts embedded in the MPD)
        refs = set()
        for lines in self.models.values():
            for ref, _, _ in _directPlacements(lines):
                if ref in self.models or ref in self.embedded or ref in refs:
                    continue
                pid = index.idFor(ref)
                if pid is not None and pid in self.protoById:
                    continue
                refs.add(ref)
        return refs

    def fetchLibrary(self):
        # One pass over the brick library for the whole file. The library's
        # objects are already prototypes (id, location, properties set by
        # prepareChunks.py), so they go straight into LEGO_Bricks.
        self.libObjects = index.appendObjects(self.libraryRefs())

    def useLibraryObject(self, ref, pid, libObj):
        # Put a library prototype in LEGO_Bricks. Returns False if it wasn't
        # prepared (wrong id or position), in which case the caller makes its
        # own prototype from the library mesh instead.
        if libObj.get(ATTR_PIECE) != pid or abs(libObj.location.x - pid) > 1e-3:
            print(f"LDRAW POINTCLOUD: WARNING, library object '{libObj.name}' isn't "
                  f"prepared (run prepareChunks.py), making a prototype from its mesh")
            return False
        if self.brickColl not in libObj.users_collection:
            self.brickColl.objects.link(libObj)   # linking a linked object is allowed
        self.protoById[pid] = libObj
        self.protoByRef.setdefault(ref, libObj)
        self.brickIds[ref] = pid
        self.newProtos += 1
        self.stats["library"] += 1
        return True

    def brickId(self, ref):
        # Piece id of a part, making its prototype in LEGO_Bricks if needed
        if ref in self.brickIds:
            return self.brickIds[ref]

        embedded = ref in self.embedded
        pid = None if embedded else index.idFor(ref)

        # A prototype from this or an earlier import
        if not embedded:
            proto = self.protoById.get(pid) if pid is not None else self.protoByRef.get(ref)
            if proto is not None:
                self.stats["shared"] += 1
                self.brickIds[ref] = proto[ATTR_PIECE]
                return proto[ATTR_PIECE]

        libObj = None if embedded else self.libObjects.get(ref)
        if libObj is not None and self.useLibraryObject(ref, pid, libObj):
            return pid

        mesh = self.findMesh(ref)
        if libObj is not None and libObj.type == 'MESH':
            # unprepared library object: same mesh, our own prototype object
            obj = bpy.data.objects.new(libObj.name, libObj.data)
            self.stats["library"] += 1
        elif mesh is not None and not self.isSpecial(ref, mesh):
            obj = bpy.data.objects.new(ref, mesh)
            self.stats["reused"] += 1
        else:
            # library already checked above, so don't let makeBrick look again
            obj = importer.makeBrick(ref, colId=16, forceNew=True, useLibrary=False)
            if obj is None:
                self.brickIds[ref] = None
                return None
            if self.isSpecial(ref, obj.data):
                # keep the file's own version out of the by-name reuse lookup
                obj.data.name = f"{ref} [{self.name}]"
            self.stats["built"] += 1
        obj.is_lego = False   # a prototype, not a brick in the scene

        if pid is None:       # not in the index, or embedded in the MPD
            pid = self.nextBuiltId
            self.nextBuiltId += 1

        # The node group finds a prototype by its x position, so the id lives
        # there; the custom properties are what later imports look it up by
        obj[ATTR_PIECE] = pid
        obj["lego_ref"] = ref
        obj["lego_embedded"] = embedded
        obj.location = (float(pid), 0.0, 0.0)
        self.brickColl.objects.link(obj)
        self.newProtos += 1
        if not embedded:
            self.protoById[pid] = obj
            self.protoByRef.setdefault(ref, obj)
        self.brickIds[ref] = pid
        return pid

    # -- models -------------------------------------------------------------

    def getModelData(self, model, inherit):
        # The point cloud of a model's own bricks, in colour `inherit`
        key = (model, inherit if self.usesInherited(model) else None)
        if key in self.modelData:
            return self.modelData[key]

        pos, rots, scales, ids, codes = [], [], [], [], []
        for ref, col, m in _directPlacements(self.models[model]):
            if ref in self.models:
                continue                      # sub-models are objects, see placeModel
            idx = self.brickId(ref)
            if idx is None:
                continue
            bl = _toBlender(m)
            q, s = _decompose(bl[:3, :3])
            pos.append(bl[:3, 3])
            rots.append(q)
            scales.append(s)
            ids.append(idx)
            codes.append(inherit if col == 16 else col)

        label = Path(model).stem
        if key[1] is not None and inherit != 16:
            label = f"{label} [{inherit}]"
        pc = self.makePointCloud(label, pos, rots, scales, ids, codes)
        self.modelData[key] = pc
        return pc

    def placeModel(self, model, inherit=16, parent=None, matrix=None,
                   collection=None, stack=()):
        # An object for one placement of a model, plus objects for every
        # sub-model it places (parented to it). Returns the object.
        obj = bpy.data.objects.new(Path(model).stem, self.getModelData(model, inherit))
        obj["lego_model"] = model
        mod = obj.modifiers.new("LEGO Instances", 'NODES')
        mod.node_group = self.getNodeGroup()
        (collection or self.collection).objects.link(obj)
        if parent is not None:
            obj.parent = parent
            obj.matrix_parent_inverse = Matrix.Identity(4)
        if matrix is not None:
            obj.matrix_basis = Matrix(matrix.tolist())
        self.modelObjs.append(obj)
        self.placed.add(model)

        stack = stack + (model,)
        for ref, col, m in _directPlacements(self.models[model]):
            if ref not in self.models:
                continue
            if ref in stack:
                print(f"LDRAW POINTCLOUD: WARNING, recursive sub-model '{ref}' skipped")
                continue
            self.placeModel(ref, inherit if col == 16 else col, obj, _toBlender(m),
                            collection, stack)
        return obj

    def makePointCloud(self, label, pos, rots, scales, ids, codes):
        n = len(pos)
        pc = bpy.data.pointclouds.new(label)
        pc.resize(n)
        self.stats["points"] += n
        if n:
            pc.attributes["position"].data.foreach_set(
                "vector", np.asarray(pos, dtype=np.float32).ravel())
            pc.attributes.new(ATTR_ROT, 'QUATERNION', 'POINT').data.foreach_set(
                "value", np.asarray(rots, dtype=np.float32).ravel())
            pc.attributes.new(ATTR_SCALE, 'FLOAT_VECTOR', 'POINT').data.foreach_set(
                "vector", np.asarray(scales, dtype=np.float32).ravel())
            pc.attributes.new(ATTR_PIECE, 'INT', 'POINT').data.foreach_set(
                "value", np.asarray(ids, dtype=np.int32))
            pc.attributes.new(ATTR_CODE, 'INT', 'POINT').data.foreach_set(
                "value", np.asarray(codes, dtype=np.int32))
            rgba = np.asarray([self.rgbaFor(c) for c in codes], dtype=np.float32)
            pc.attributes.new(ATTR_COLOR, 'FLOAT_COLOR', 'POINT').data.foreach_set(
                "color", rgba.ravel())
            # Small points so the bare cloud is still visible with the modifier off
            radius = pc.attributes.get("radius") or pc.attributes.new("radius", 'FLOAT', 'POINT')
            radius.data.foreach_set("value", np.full(n, 2.0, np.float32))
        return pc

    def getNodeGroup(self):
        # One group for every import; it reads LEGO_Bricks live, so new
        # prototypes need no changes to it
        if self.nodeGroup is None:
            ng = bpy.data.node_groups.get(NODE_GROUP)
            if ng is None or not ng.get("lego_piece_lookup"):
                ng = buildNodeGroup(NODE_GROUP, self.brickColl)
                ng["lego_piece_lookup"] = True
            self.nodeGroup = ng
        return self.nodeGroup

def _getBrickCollection():
    # The shared prototype collection, made on first use
    coll = bpy.data.collections.get(BRICK_COLL)
    if coll is None:
        coll = bpy.data.collections.new(BRICK_COLL)
        coll.hide_render = True
        coll.use_fake_user = True   # survives even if every import is deleted
    scene = bpy.context.scene
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll

# ---------------------------------------------------------------------------
# Geometry nodes
# ---------------------------------------------------------------------------

def _socketId(ng, name):
    # Modifier key for a group input, e.g. "Socket_1"
    return next(i.identifier for i in ng.interface.items_tree
                if i.item_type == 'SOCKET' and i.in_out == 'INPUT' and i.name == name)

def setModifierInput(mod, name, value):
    # Blender 5.2 keeps modifier inputs under mod.properties.inputs.<id>.value;
    # older versions used ID properties (mod["Socket_1"])
    sid = _socketId(mod.node_group, name)
    props = getattr(mod, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        getattr(props.inputs, sid).value = value
    else:
        mod[sid] = value

def buildNodeGroup(name, brickColl):
    # Points -> Instance on Points (brick picked by lego_piece from the shared
    # collection, rotated and scaled by the point attributes) -> col, colExists
    # and lego_code stored on the instances.
    #
    # Piece id -> instance index: every prototype sits at x = its piece id, so a
    # second Collection Info that keeps the children's transforms, turned into
    # points, is a table of ids in the same order as the instances. Sample
    # Nearest at (lego_piece, 0, 0) returns the matching index. Ids are whole
    # numbers well under 2^24, so the float match is exact.
    ng = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    realizeSock = ng.interface.new_socket("Realize", in_out='INPUT', socket_type='NodeSocketBool')
    realizeSock.default_value = False
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')

    nodes, links = ng.nodes, ng.links

    def node(kind, x, y, label=None, **props):
        n = nodes.new(kind)
        n.location = (x, y)
        if label:
            n.label = label
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def named(attr, dataType, x, y):
        n = node("GeometryNodeInputNamedAttribute", x, y, data_type=dataType)
        n.inputs["Name"].default_value = attr
        return n

    def store(geo, attr, dataType, x, label):
        n = node("GeometryNodeStoreNamedAttribute", x, 0, label=label,
                 data_type=dataType, domain='INSTANCE')
        n.inputs["Name"].default_value = attr
        links.new(geo, n.inputs["Geometry"])
        return n

    gIn = node("NodeGroupInput", -900, 0)
    gOut = node("NodeGroupOutput", 1400, 0)

    def collInfo(y, label, reset):
        n = node("GeometryNodeCollectionInfo", -900, y, label=label,
                 transform_space='ORIGINAL')
        n.inputs["Collection"].default_value = brickColl
        n.inputs["Separate Children"].default_value = True
        n.inputs["Reset Children"].default_value = reset
        return n

    coll = collInfo(-150, "Bricks", True)          # what gets instanced
    table = collInfo(-450, "Piece id table", False)  # keeps x = piece id

    tablePts = node("GeometryNodeInstancesToPoints", -650, -450, label="Ids as points")
    links.new(table.outputs["Instances"], tablePts.inputs["Instances"])

    pieceAttr = named(ATTR_PIECE, 'INT', -900, -700)
    samplePos = node("ShaderNodeCombineXYZ", -650, -700)
    links.new(pieceAttr.outputs["Attribute"], samplePos.inputs["X"])

    lookup = node("GeometryNodeSampleNearest", -450, -500, label="Piece id -> index",
                  domain='POINT')
    links.new(tablePts.outputs["Points"], lookup.inputs["Geometry"])
    links.new(samplePos.outputs["Vector"], lookup.inputs["Sample Position"])

    rotAttr = named(ATTR_ROT, 'QUATERNION', -600, -850)
    scaleAttr = named(ATTR_SCALE, 'FLOAT_VECTOR', -600, -1000)

    inst = node("GeometryNodeInstanceOnPoints", -250, 0)
    inst.inputs["Pick Instance"].default_value = True
    links.new(gIn.outputs["Geometry"], inst.inputs["Points"])
    links.new(coll.outputs["Instances"], inst.inputs["Instance"])
    links.new(lookup.outputs["Index"], inst.inputs["Instance Index"])
    links.new(rotAttr.outputs["Attribute"], inst.inputs["Rotation"])
    links.new(scaleAttr.outputs["Attribute"], inst.inputs["Scale"])

    # Brick colour onto the instances as "col"
    colAttr = named(ATTR_COLOR, 'FLOAT_COLOR', 0, -300)
    storeCol = store(inst.outputs["Instances"], ATTR_COLOR, 'FLOAT_COLOR', 100, "Store col")
    links.new(colAttr.outputs["Attribute"], storeCol.inputs["Value"])

    # colExists = True on every instance
    storeExists = store(storeCol.outputs["Geometry"], ATTR_COL_EXISTS, 'BOOLEAN', 400,
                        "Store colExists")
    storeExists.inputs["Value"].default_value = True

    codeAttr = named(ATTR_CODE, 'INT', 600, -300)
    storeCode = store(storeExists.outputs["Geometry"], ATTR_CODE, 'INT', 700,
                      "Store lego_code")
    links.new(codeAttr.outputs["Attribute"], storeCode.inputs["Value"])

    realize = node("GeometryNodeRealizeInstances", 950, -150)
    links.new(storeCode.outputs["Geometry"], realize.inputs["Geometry"])

    switch = node("GeometryNodeSwitch", 1200, 0, input_type='GEOMETRY')
    links.new(gIn.outputs["Realize"], switch.inputs["Switch"])
    links.new(storeCode.outputs["Geometry"], switch.inputs["False"])
    links.new(realize.outputs["Geometry"], switch.inputs["True"])
    links.new(switch.outputs[0], gOut.inputs["Geometry"])
    return ng

# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def _packTempImages(tmp):
    # Textures loaded from an MPD's unpacked files would vanish with the temp folder
    tmp = str(Path(tmp).resolve())
    for img in bpy.data.images:
        try:
            if img.filepath and str(Path(bpy.path.abspath(img.filepath)).resolve()).startswith(tmp):
                img.pack()
        except (RuntimeError, OSError):
            print(f"LDRAW POINTCLOUD: WARNING, could not pack image '{img.name}'")

def importLdrPointCloud(ldrPath, collection):
    """Import an .ldr/.mpd into `collection` (already linked to the scene).
    Returns (main model object or None, import stats dict)."""
    models = importer._readModels(ldrPath)
    if not any(models.values()):
        return None, {}
    main = next(iter(models))
    parts = {n: ls for n, ls in models.items() if n != main and importer._isPart(ls)}

    state = _Import(collection)
    state.models = {n: ls for n, ls in models.items() if n not in parts}
    state.embedded = set(parts)

    with tempfile.TemporaryDirectory() as tmp:
        for name, lines in parts.items():
            p = Path(tmp) / name                      # keeps s/, 48/ etc.
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        library.setOverlay(tmp)
        try:
            state.fetchLibrary()
            mainObj = state.placeModel(main)
            # Sub-models nothing places still get their own point cloud
            unused = [m for m in state.models if m not in state.placed]
            if unused:
                unusedColl = bpy.data.collections.new(f"{state.name}_unused")
                collection.children.link(unusedColl)
                for m in unused:
                    if m not in state.placed:     # may have been placed by another unused one
                        state.placeModel(m, collection=unusedColl)
                _excludeFromViewLayers(unusedColl)
            if parts:
                _packTempImages(tmp)
        finally:
            library.setOverlay(None)

    _excludeFromViewLayers(state.brickColl)
    state.stats["objects"] = len(state.modelObjs)
    state.stats["models"] = len(state.modelData)
    state.stats["bricks"] = len(state.brickIds)
    state.stats["newPrototypes"] = state.newProtos
    return mainObj, state.stats

# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class LEGO_OT_ImportLdrPointCloud(bpy.types.Operator, ImportHelper):
    bl_idname = "lego.import_ldr_pointcloud"
    bl_label = "Import LDR (Point Cloud)"
    bl_description = "Import a lego (.ldr/.mpd) model as instanced point clouds, one per model"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".ldr"
    filter_glob: StringProperty(default="*.ldr;*.mpd", options={'HIDDEN'})

    def execute(self, context):
        start = time.perf_counter()
        name = Path(self.filepath).stem
        coll = bpy.data.collections.new(name)
        context.scene.collection.children.link(coll)
        obj, stats = importLdrPointCloud(self.filepath, coll)
        if obj is None:
            self.report({'WARNING'}, "No models found in file")
            return {'CANCELLED'}

        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        ms = round((time.perf_counter() - start) * 1000)
        self.report({'INFO'},
                    f"Imported {stats['points']} bricks in {stats['models']} point clouds "
                    f"({stats['shared']} already loaded, {stats['library']} from library, "
                    f"{stats['reused']} reused, "
                    f"{stats['built']} built) in {ms}ms")
        return {'FINISHED'}

def importMenu(self, context):
    self.layout.operator(LEGO_OT_ImportLdrPointCloud.bl_idname,
                         text="LEGO LDR file, point cloud (.ldr)")

def register():
    bpy.utils.register_class(LEGO_OT_ImportLdrPointCloud)
    bpy.types.TOPBAR_MT_file_import.append(importMenu)

def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(importMenu)
    bpy.utils.unregister_class(LEGO_OT_ImportLdrPointCloud)
