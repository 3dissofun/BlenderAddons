"""Debug display of brick snap points, as gizmos on the selected bricks.

For each snap:
- a cross in the plane across the snap's axis, with arms as long as its radius.
  Blue = male, red = female.
- cylinder snaps get a cross at both ends of every section of their profile,
  each sized to that section's own radius (so a stepped profile such as 6141's
  top, R 10 3  R 6 4, shows the wide collar and then the narrower stud, as
  LDCad draws it), plus a line along the axis over the whole profile.
  Snap.radius is the largest section, so it is only used for other kinds.

Both are tiny custom gizmos (one LINES shape each). Their size is baked into
the gizmo matrix and scale_basis stays 1, so they are drawn in world units and
match the brick, not the screen.

Only selected bricks are shown, since every snap is a few gizmos.
WindowManager.lego_show_snaps turns them on and off (see SnapUiProps.py).
"""
import bpy
from mathutils import Matrix

MALE_COL = (0.15, 0.45, 1.0)
FEMALE_COL = (1.0, 0.2, 0.2)
MIN_CROSS = 0.0  # Blender units, for snaps with no radius

CROSS = "VIEW3D_GT_lego_cross"
LINE = "VIEW3D_GT_lego_line"

class VIEW3D_GT_lego_cross(bpy.types.Gizmo):
    # A cross in the gizmo's local XY plane, arms reaching 1 unit from the centre.
    # Scale X/Y in matrix_basis to set the arm length.
    bl_idname = CROSS

    def setup(self):
        self.shape = self.new_custom_shape('LINES', (
            (-1.0, 0.0, 0.0), (1.0, 0.0, 0.0),
            (0.0, -1.0, 0.0), (0.0, 1.0, 0.0),
        ))

    def draw(self, context):
        self.draw_custom_shape(self.shape)

class VIEW3D_GT_lego_line(bpy.types.Gizmo):
    # A line from the gizmo origin along its local +Z, 1 unit long.
    # Scale Z in matrix_basis to set the length.
    bl_idname = LINE

    def setup(self):
        self.shape = self.new_custom_shape('LINES', ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)))

    def draw(self, context):
        self.draw_custom_shape(self.shape)

def _hasSnaps(obj):
    return obj.type == 'MESH' and obj.is_lego and len(obj.data.lego_snaps) > 0

def _crossScale(radius):
    size = max(radius, MIN_CROSS)
    return Matrix.Diagonal((size, size, 1.0, 1.0))

def _gizmoSpecs(obj):
    # (gizmo type, world matrix, colour) for every gizmo of a brick.
    # Gizmo frames use local Z as the snap axis: columns (snap X, snap Z, snap -Y).
    mw = obj.matrix_world
    specs = []
    for snap in obj.data.lego_snaps:
        m = Matrix([snap.matrix[i * 4:(i + 1) * 4] for i in range(4)])
        x, y, z = (m.col[i].xyz for i in range(3))
        axis = -y
        frame = Matrix((x, z, axis)).transposed().to_4x4()
        col = MALE_COL if snap.gender == 'M' else FEMALE_COL

        def at(point):
            f = frame.copy()
            f.translation = point
            return mw @ f

        origin = m.translation
        secs = [(sec.radius, sec.length) for sec in snap.secs]
        length = sum(l for _, l in secs)
        if snap.kind == 'CYL' and secs and length > 0:
            start = origin - axis * (length / 2) if snap.center else origin
            # A cross at both ends of each section, at that section's radius.
            # Where two sections of the same radius meet, one cross is enough.
            pos, prev = 0.0, None
            for radius, secLen in secs:
                for t in (pos, pos + secLen):
                    key = (round(t, 4), round(radius, 4))
                    if key != prev:
                        specs.append((CROSS, at(start + axis * t) @ _crossScale(radius), col))
                    prev = key
                pos += secLen
            specs.append((LINE, at(start) @ Matrix.Diagonal((1.0, 1.0, length, 1.0)), col))
        else:
            specs.append((CROSS, at(origin) @ _crossScale(snap.radius), col))
    return specs

def _allSpecs(context):
    return [s for o in context.selected_objects if _hasSnaps(o) for s in _gizmoSpecs(o)]

class VIEW3D_GGT_lego_snaps(bpy.types.GizmoGroup):
    bl_idname = "VIEW3D_GGT_lego_snaps"
    bl_label = "LEGO Snaps"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT'}

    @classmethod
    def poll(cls, context):
        return (context.window_manager.lego_show_snaps
                and any(_hasSnaps(o) for o in context.selected_objects))

    def setup(self, context):
        self.layout_key = None
        self.refresh(context)

    def refresh(self, context):
        # Rebuild the gizmo set when the selection (or its snaps) changes
        kinds = tuple(s[0] for s in _allSpecs(context))
        if kinds == self.layout_key:
            return
        self.gizmos.clear()
        for kind in kinds:
            gz = self.gizmos.new(kind)
            gz.use_draw_scale = False  # world units, not screen units
            gz.scale_basis = 1.0       # size lives in matrix_basis
            gz.hide_select = True      # debug only, never grabs the mouse
            gz.alpha = gz.alpha_highlight = 1.0
        self.layout_key = kinds

    def draw_prepare(self, context):
        # Every redraw: follow the bricks as they move
        specs = _allSpecs(context)
        if tuple(s[0] for s in specs) != self.layout_key:
            return  # selection changed; refresh will rebuild first
        for gz, (kind, mat, col) in zip(self.gizmos, specs):
            gz.matrix_basis = mat
            gz.color = gz.color_highlight = col

CLASSES = (VIEW3D_GT_lego_cross, VIEW3D_GT_lego_line, VIEW3D_GGT_lego_snaps)

def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
