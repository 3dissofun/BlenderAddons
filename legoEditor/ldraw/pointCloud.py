"""
Import an LDR/MPD as point clouds instead of one object per brick.

Layout of an import called "<name>":

    <name>                      collection, linked to the scene
    ├─ main                     point cloud object, the main model's own bricks
    │  ├─ wheel                 sub-model placements: objects sharing the
    │  └─ wheel.001             sub-model's point cloud, parented to the model
    │                           that places them, transformed by the placement
    ├─ <name>_bricks            ONE collection of brick prototypes (excluded
    │  ├─ LP00000 3001.dat      from the view layer), shared by every point
    │  └─ LP00001 3641.dat      cloud in the import
    └─ <name>_unused            sub-models in the file that nothing places
                                (at the origin, excluded from the view layer)

Every model in the file has its own point cloud, holding only that model's
own bricks. A sub-model placed inside another model is an object that uses
the sub-model's point cloud, so a model placed many times shares one point
cloud. If a sub-model inherits its colour (code 16) and is placed in several
colours, it gets one point cloud per colour, e.g. "wheel [4]" and "wheel [1]".

Point attributes (POINT domain, Blender space, LDU):
    position     the brick's origin
    rotation     QUATERNION, brick rotation (mirroring folded into scale)
    scale        FLOAT_VECTOR, per axis scale, negative x for mirrored bricks
    lego_proto   INT, index of the brick in <name>_bricks
    col          FLOAT_COLOR, scene linear RGBA (what obj.color holds for a brick)
    lego_code    INT, LDraw colour code

The geometry nodes group (LEGO_PointCloud_<name>) on every point cloud
instances the right brick onto each point, then stores on the INSTANCE domain:
    col          FLOAT_COLOR, the brick colour
    colExists    BOOLEAN, always True
    lego_code    INT, the LDraw colour code
Read them in a shader with an Attribute node set to "Instancer". The group's
"Realize" input realizes the instances instead.

Bricks: an existing brick mesh in the file is reused (by part name, e.g.
"3001.dat" or "3001"). A part is only built if no mesh exists, or if it is
special: textured, or embedded in the MPD (those are built fresh for each
import, so a file's own version never gets mixed up with the library part).
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

LD_TO_BL = importer.LD_TO_BL
BL_FROM_LD = np.linalg.inv(LD_TO_BL)

PROTO_PREFIX = "LP"          # brick prototypes are named LP00000 <part>
NODE_GROUP_PREFIX = "LEGO_PointCloud_"
FALLBACK_RGBA = (0.5, 0.5, 0.5, 1.0)

ATTR_PROTO = "lego_proto"
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
        self.bricks = []          # brick prototypes, index == lego_proto
        self.brickIds = {}        # {part ref: index}
        self.modelData = {}       # {(model, inherited colour or None): PointCloud}
        self.modelObjs = []       # every point cloud object made
        self.placed = set()       # models placed at least once
        self.inherits = {}        # {model: uses colour 16}
        self.rgba = {}            # {colour code: rgba}
        self.stats = {"reused": 0, "built": 0, "points": 0}

        self.brickColl = bpy.data.collections.new(f"{self.name}_bricks")
        collection.children.link(self.brickColl)
        self.brickColl.hide_render = True
        self.brickColl.hide_viewport = True
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

    def brickId(self, ref):
        # Index of a part in the shared brick collection, building it if needed
        if ref in self.brickIds:
            return self.brickIds[ref]

        mesh = self.findMesh(ref)
        if mesh is not None and not self.isSpecial(ref, mesh):
            obj = bpy.data.objects.new(ref, mesh)
            self.stats["reused"] += 1
        else:
            obj = importer.makeBrick(ref, colId=16, forceNew=True)
            if obj is None:
                self.brickIds[ref] = None
                return None
            if self.isSpecial(ref, obj.data):
                # keep the file's own version out of the by-name reuse lookup
                obj.data.name = f"{ref} [{self.name}]"
            self.stats["built"] += 1
        obj.is_lego = False   # a prototype, not a brick in the scene

        # Collection Info sorts children by name, so the zero padded index at
        # the front of the name is what keeps lego_proto pointing at the right one
        idx = len(self.bricks)
        obj.name = f"{PROTO_PREFIX}{idx:05d} {ref}"
        self.brickColl.objects.link(obj)
        self.bricks.append(obj)
        self.brickIds[ref] = idx
        return idx

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
            pc.attributes.new(ATTR_PROTO, 'INT', 'POINT').data.foreach_set(
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
        if self.nodeGroup is None:
            self.nodeGroup = buildNodeGroup(NODE_GROUP_PREFIX + self.name, self.brickColl)
        return self.nodeGroup

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
    # Points -> Instance on Points (brick picked by lego_proto from the shared
    # collection, rotated and scaled by the point attributes) -> col, colExists
    # and lego_code stored on the instances
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

    coll = node("GeometryNodeCollectionInfo", -600, -150, label="Bricks",
                transform_space='ORIGINAL')
    coll.inputs["Collection"].default_value = brickColl
    coll.inputs["Separate Children"].default_value = True
    coll.inputs["Reset Children"].default_value = True

    protoAttr = named(ATTR_PROTO, 'INT', -600, -400)
    rotAttr = named(ATTR_ROT, 'QUATERNION', -600, -550)
    scaleAttr = named(ATTR_SCALE, 'FLOAT_VECTOR', -600, -700)

    inst = node("GeometryNodeInstanceOnPoints", -250, 0)
    inst.inputs["Pick Instance"].default_value = True
    links.new(gIn.outputs["Geometry"], inst.inputs["Points"])
    links.new(coll.outputs["Instances"], inst.inputs["Instance"])
    links.new(protoAttr.outputs["Attribute"], inst.inputs["Instance Index"])
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
    state.stats["bricks"] = len(state.bricks)
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
                    f"({stats['reused']} parts reused, {stats['built']} built) in {ms}ms")
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
