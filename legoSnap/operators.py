import bpy
from bpy_extras import view3d_utils

import time
import numpy as np
import random
from mathutils import Matrix, Quaternion, Vector
from mathutils.kdtree import KDTree

from . import brickBuilder, datParser

class LE_OT_ImportRandom(bpy.types.Operator):
    bl_idname = "lego.import_random"
    bl_label = "Import Brick"
    bl_options = {'REGISTER','UNDO'}

    def execute(self,context):
        start = time.perf_counter()
        wm = bpy.context.window_manager

        brickId = random.randrange()
        pieceLibrary = bpy.context.preferences.addons[__package__].preferences.pieceLibrary
        shadowLibrary = bpy.context.preferences.addons[__package__].preferences.shadowLibrary

        datParser.libraryDir = pieceLibrary
        datParser.shadowDir = shadowLibrary
        obj = brickBuilder.makeBrick(brickId)
        if obj:
            duration = round(time.perf_counter() - start,4)*1000
            self.report({'INFO'},f"Brick built in {duration}ms")
            context.scene.collection.objects.link(obj)
            return {'FINISHED'}
        else:
            self.report({'WARNING'},"No brick found for given ID")
            return {'CANCELLED'}

class LE_OT_ImportBrick(bpy.types.Operator):
    bl_idname = "lego.import_brick"
    bl_label = "Import Brick"
    bl_options = {'REGISTER','UNDO'}

    def execute(self,context):
        start = time.perf_counter()
        wm = bpy.context.window_manager
        brickId = wm.brick_id
        pieceLibrary = bpy.context.preferences.addons[__package__].preferences.pieceLibrary
        shadowLibrary = bpy.context.preferences.addons[__package__].preferences.shadowLibrary
        if not brickId:
            self.report({'WARNING'},"Please provide a brick ID")
            return {'CANCELLED'}

        datParser.libraryDir = pieceLibrary
        datParser.shadowDir = shadowLibrary
        obj = brickBuilder.makeBrick(brickId)
        if obj:
            duration = round(time.perf_counter() - start,4)*1000
            self.report({'INFO'},f"Brick built in {duration}ms")
            context.scene.collection.objects.link(obj)
            return {'FINISHED'}
        else:
            self.report({'WARNING'},"No brick found for given ID")
            return {'CANCELLED'}

# (maleShape, femaleShape) pairs that fit when the radius matches
SHAPE_FITS = {("R", "R"), ("A", "A"), ("A", "R"), ("S", "S")}

def areSnapsCompatible(snapA, snapB):
    # kind must match and genders must be opposite
    if snapA.kind != snapB.kind:
        return False
    genderA, genderB = snapA.gender.upper(), snapB.gender.upper()
    if {genderA, genderB} != {"M", "F"}:
        return False
    male, female = (snapA, snapB) if genderA == "M" else (snapB, snapA)

    # clips / fingers: single radius must match
    if male.radius and female.radius:
        try:
            if abs(float(male.radius) - float(female.radius)) > 1e-3:
                return False
        except ValueError:
            pass

    # cylinders: secs is "shape radius length" repeated, at least one male section must fit a female one
    try:
        mTok, fTok = male.secs.split(), female.secs.split()
        maleSecs = list(zip((s.upper() for s in mTok[0::3]), map(float, mTok[1::3])))
        femaleSecs = list(zip((s.upper() for s in fTok[0::3]), map(float, fTok[1::3])))
    except ValueError:
        return True  # unreadable secs: fall back to kind + gender
    if not maleSecs or not femaleSecs:
        return True

    return any(abs(mr - fr) < 1e-3 and (ms == fs or (ms, fs) in SHAPE_FITS)
               for ms, mr in maleSecs for fs, fr in femaleSecs)

def snapCorrection(mySnapWorld, theirSnapWorld):
    # matrix that, applied on the left, seats mySnap onto theirSnap
    snapAxis = Vector((0, 0, -1))  # LDraw +Y after the LDtoBL conversion
    myAxis = (mySnapWorld.to_3x3() @ snapAxis).normalized()
    theirAxis = (theirSnapWorld.to_3x3() @ snapAxis).normalized()

    myPos = mySnapWorld.translation
    rot = myAxis.rotation_difference(theirAxis).to_matrix().to_4x4()
    pivot = Matrix.Translation(myPos) @ rot @ Matrix.Translation(-myPos)
    move = Matrix.Translation(theirSnapWorld.translation - myPos)
    return move @ pivot

def hasAncestorIn(obj, objSet):
    p = obj.parent
    while p:
        if p in objSet:
            return True
        p = p.parent
    return False

def projectToRegion(region, rv3d, pts):
    # pts: (N, 3) world positions -> (N, 2) region pixel coords + mask of points in front of the view
    # same maths as view3d_utils.location_3d_to_region_2d, vectorised
    persp = np.array(rv3d.perspective_matrix)
    clip = np.hstack([pts, np.ones((len(pts), 1))]) @ persp.T
    w = clip[:, 3]
    valid = w > 1e-6
    w = np.where(valid, w, 1.0)  # rows behind the view are masked out anyway
    x = region.width * 0.5 * (1.0 + clip[:, 0] / w)
    y = region.height * 0.5 * (1.0 + clip[:, 1] / w)
    return np.column_stack([x, y]), valid

class LE_OT_SnapBuild(bpy.types.Operator):
    bl_idname = "lego.snap_build"
    bl_label = "Snap Build"
    bl_options = {'REGISTER', 'UNDO'}

    def mouseCoord(self, event):
        return (event.mouse_x - self.region.x, event.mouse_y - self.region.y)

    def mouseTo3d(self, event):
        return view3d_utils.region_2d_to_location_3d(
            self.region, self.rv3d, self.mouseCoord(event), self.startLoc)

    def invoke(self, context, event):
        active = context.active_object
        if not active or context.area.type != 'VIEW_3D':
            self.report({'WARNING'}, "Needs an active object in the 3D viewport")
            return {'CANCELLED'}

        self.area = context.area
        self.region = next(r for r in self.area.regions if r.type == 'WINDOW')
        self.rv3d = self.area.spaces.active.region_3d

        selected = set(context.selected_objects) | {active}
        # children follow their parent, so only drive the top of each selected hierarchy
        self.movers = [o for o in selected if not hasAncestorIn(o, selected)]
        movingSet = set(self.movers)
        self.startMatrices = {o.name: o.matrix_world.copy() for o in self.movers}

        # grab relative to the active object, like G does
        self.startLoc = active.matrix_world.translation.copy()
        self.grabOffset = self.startLoc - self.mouseTo3d(event)

        # split every snap in the scene into "moving with me" and "stationary target"
        # entries: (objName, snapIndex, worldSnapMatrix at start, kind, gender)
        self.movingSnaps = []
        self.targets = []
        for o in context.visible_objects:
            if o.type != 'MESH' or not len(o.data.lego_snaps):
                continue
            moving = o in movingSet or hasAncestorIn(o, movingSet)
            mw = o.matrix_world
            for j, s in enumerate(o.data.lego_snaps):
                entry = (o.name, j, mw @ snapMatrix(s), s)
                (self.movingSnaps if moving else self.targets).append(entry)

        # flat arrays for vectorised projection
        self.targetPos = np.array([e[2].translation for e in self.targets], dtype=float).reshape(-1, 3)
        self.movingPos = np.array([e[2].translation for e in self.movingSnaps], dtype=float).reshape(-1, 3)

        self.viewKey = None  # forces a rebuild on the first move
        self.tree = None
        self.targetDepth = None

        # UX Feel
        self.snapPixelRadius = 50
        self.stickiness = 8
        self.depthScale = 0.48
        self.depthPenalty = 15
        self.lockedPair = None  # (movingSnapIndex, targetIndex)

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def rebuildScreenIndex(self):
        # project every target once and bucket the on-screen ones into KD-trees by (kind, gender)
        region, rv3d = self.region, self.rv3d
        self.trees = {}
        if not len(self.targetPos):
            return

        scr, valid = projectToRegion(region, rv3d, self.targetPos)
        view = np.array(rv3d.view_matrix)
        self.targetDepth = -(self.targetPos @ view[:3, :3].T + view[:3, 3])[:, 2]

        r = self.snapPixelRadius
        onScreen = (valid
                    & (scr[:, 0] > -r) & (scr[:, 0] < region.width + r)
                    & (scr[:, 1] > -r) & (scr[:, 1] < region.height + r))

        idxs = np.flatnonzero(onScreen)
        self.tree = None
        if len(idxs):
            self.tree = KDTree(len(idxs))
            for t in idxs:
                self.tree.insert((scr[t, 0], scr[t, 1], 0.0), int(t))
            self.tree.balance()

    def findScreenSnap(self, offset):
        # rebuild the screen index only when the view has changed (orbit / pan / zoom / resize)
        viewKey = (tuple(tuple(row) for row in self.rv3d.perspective_matrix),
                   self.region.width, self.region.height)
        if viewKey != self.viewKey:
            self.viewKey = viewKey
            self.rebuildScreenIndex()
        if self.tree is None or not len(self.movingPos):
            return None

        region, r = self.region, self.snapPixelRadius
        scr, valid = projectToRegion(region, self.rv3d, self.movingPos + np.array(offset))
        valid &= ((scr[:, 0] > -r) & (scr[:, 0] < region.width + r)
                  & (scr[:, 1] > -r) & (scr[:, 1] < region.height + r))

        hits = []  # (pixelDist, depth, movingIndex, targetIndex)
        for k in np.flatnonzero(valid):
            mySnap = self.movingSnaps[k][3]
            for _, t, d in self.tree.find_range((scr[k, 0], scr[k, 1], 0.0), r):
                if areSnapsCompatible(mySnap, self.targets[t][3]):
                    hits.append((d, self.targetDepth[t], int(k), t))        

        if not hits:
            return None

        nearest = min(h[1] for h in hits)
        best = None
        for d, depth, k, t in hits:
            score = d + self.depthPenalty * (depth - nearest) / self.depthScale
            if (k, t) == self.lockedPair:
                score -= self.stickiness
            if best is None or score < best[0]:
                best = (score, k, t)
        return best

    def applyToGroup(self, m):
        for o in self.movers:
            o.matrix_world = m @ self.startMatrices[o.name]

    def modal(self, context, event):
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}

        if event.type == 'MOUSEMOVE':
            offset = self.mouseTo3d(event) + self.grabOffset - self.startLoc
            delta = Matrix.Translation(offset)

            best = self.findScreenSnap(offset)
            if best:
                _, k, t = best
                mySnap = delta @ self.movingSnaps[k][2]  # that snap at the free position
                corr = snapCorrection(mySnap, self.targets[t][2])
                self.applyToGroup(corr @ delta)
                self.lockedPair = (k, t)
            else:
                self.applyToGroup(delta)
                self.lockedPair = None

        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            return {'FINISHED'}

        elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self.applyToGroup(Matrix.Identity(4))  # back to start matrices
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

def snapMatrix(s):
    return Matrix.Translation(Vector(s.location)) @ Quaternion(s.rotation).to_matrix().to_4x4()

def getSnapBricks(context):
    # selected objects that carry snap data
    return [o for o in context.selected_objects
            if o.type == 'MESH' and len(o.data.lego_snaps)]

class LE_GGT_SnapPoints(bpy.types.GizmoGroup):
    bl_idname = "LE_GGT_snap_points"
    bl_label = "Lego Snap Points"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT', 'SHOW_MODAL_ALL'}

    maleCol = (1.0, 0.8, 0.0)
    femaleCol = (1.0, 0.8, 0.0)

    @classmethod
    def poll(cls, context):
        return context.window_manager.lego_show_snaps and bool(getSnapBricks(context))

    def setup(self, context):
        self.layoutKey = None
        self.sync(context)

    def refresh(self, context):
        # called when selection / data changes: rebuild only if the layout differs
        self.sync(context)

    def draw_prepare(self, context):
        # called every redraw: keep gizmos glued to their bricks as they move
        # entries store names/indices, not live references, which go stale when an object is deleted
        for gz, (objName, i) in zip(self.gizmos, self.entries):
            obj = bpy.data.objects.get(objName)
            if obj is None:
                # object was deleted; refresh() will rebuild the gizmos
                gz.hide = True
                continue
            gz.hide = False
            gz.matrix_basis = obj.matrix_world @ snapMatrix(obj.data.lego_snaps[i])

    def sync(self, context):
        bricks = getSnapBricks(context)
        key = tuple((o.name, len(o.data.lego_snaps)) for o in bricks)
        if key == self.layoutKey:
            return
        self.layoutKey = key

        for gz in list(self.gizmos):
            self.gizmos.remove(gz)

        self.entries = []
        for obj in bricks:
            for i, s in enumerate(obj.data.lego_snaps):
                gz = self.gizmos.new("GIZMO_GT_move_3d")
                gz.draw_style = 'RING_2D'
                gz.draw_options = {'FILL','ALIGN_VIEW'}
                gz.hide_select = True
                gz.scale_basis = 0.04
                isMale = s.gender.upper() == 'M'
                gz.color = self.maleCol if isMale else self.femaleCol
                gz.alpha = 1.0
                self.entries.append((obj.name, i))

class LE_OT_ToggleSnaps(bpy.types.Operator):
    bl_idname = "lego.toggle_snaps"
    bl_label = "Toggle Snap Points"
    bl_description = "Toggle drawing of snap points on selected bricks"

    def execute(self, context):
        wm = context.window_manager
        wm.lego_show_snaps = not wm.lego_show_snaps
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return {'FINISHED'}


classes = (LE_OT_SnapBuild, LE_OT_ImportBrick, LE_OT_ToggleSnaps, LE_GGT_SnapPoints)

addonKeymaps = []

def register():
    bpy.types.WindowManager.lego_show_snaps = bpy.props.BoolProperty(default=False)
    for c in classes:
        bpy.utils.register_class(c)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:  # None when Blender runs in background mode
        km = kc.keymaps.new(name='Object Mode', space_type='EMPTY')
        kmi = km.keymap_items.new(LE_OT_SnapBuild.bl_idname, 'G', 'PRESS', shift=True)
        addonKeymaps.append((km, kmi))

def unregister():
    for km, kmi in addonKeymaps:
        km.keymap_items.remove(kmi)
    addonKeymaps.clear()

    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.WindowManager.lego_show_snaps
