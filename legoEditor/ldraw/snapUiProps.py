import bpy

# ---------------------------------------------------------------------------
# Snap storage
#
# Snaps live on the mesh datablock (Mesh.lego_snaps), so every object sharing a
# brick mesh shares its snaps. Values are in the mesh's local space, in Blender
# units: world space = obj.matrix_world @ snap matrix.
# ---------------------------------------------------------------------------

class LegoSnapSection(bpy.types.PropertyGroup):
    # One block of a cylinder's profile, listed from the snap origin along its -Y.
    # For fingers: one finger, shape = its gender ("M"/"F"), length = its width.
    shape: bpy.props.StringProperty(name="Shape", description="R, A, S, _L or L_ (fingers: M or F)")
    radius: bpy.props.FloatProperty(name="Radius", unit='LENGTH')
    length: bpy.props.FloatProperty(name="Length", unit='LENGTH')

class LegoSnap(bpy.types.PropertyGroup):
    kind: bpy.props.EnumProperty(
        name="Kind",
        items=[
            ('CYL', "Cylinder", "Stud, hole, pin or axle"),
            ('CLP', "Clip", "Clip (always female, takes a male cylinder)"),
            ('FGR', "Fingers", "Interlocking hinge fingers"),
            ('GEN', "Generic", "Odd connection matched by group"),
            ('SPH', "Sphere", "Ball joint (legacy)"),
        ],
        default='CYL',
    )
    gender: bpy.props.EnumProperty(
        name="Gender",
        items=[('M', "Male", ""), ('F', "Female", "")],
        default='M',
    )
    group: bpy.props.StringProperty(name="Group")
    ids: bpy.props.StringProperty(name="IDs", description="Comma separated, lowercase")
    # 4x4 row-major, rotation + translation. The snap points along its local -Y
    matrix: bpy.props.FloatVectorProperty(
        name="Matrix", size=16,
        default=(1, 0, 0, 0,  0, 1, 0, 0,  0, 0, 1, 0,  0, 0, 0, 1),
    )
    secs: bpy.props.CollectionProperty(type=LegoSnapSection)
    capA: bpy.props.BoolProperty(name="Cap A", description="End at the snap origin is closed")
    capB: bpy.props.BoolProperty(name="Cap B", description="Far end is closed")
    center: bpy.props.BoolProperty(name="Center", description="Profile is centred on the origin")
    slide: bpy.props.BoolProperty(name="Slide")
    radius: bpy.props.FloatProperty(name="Radius", unit='LENGTH')
    length: bpy.props.FloatProperty(name="Length", unit='LENGTH')
    bounding: bpy.props.StringProperty(name="Bounding", description="Generic bounding shape, raw LDraw units")
    extra: bpy.props.StringProperty(name="Extra", description="Unrecognised parameters as JSON")

# ---------------------------------------------------------------------------
# Snap display
# ---------------------------------------------------------------------------

def _redrawViews(self, context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

CLASSES = (LegoSnapSection, LegoSnap)

def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)

    bpy.types.Mesh.lego_snaps = bpy.props.CollectionProperty(type=LegoSnap)

    bpy.types.WindowManager.lego_show_snaps = bpy.props.BoolProperty(
        name="Show Snaps",
        description="Draw snap debug gizmos on selected bricks",
        default=False,
        update=_redrawViews,
        )

def unregister():
    del bpy.types.WindowManager.lego_show_snaps
    del bpy.types.Mesh.lego_snaps

    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
