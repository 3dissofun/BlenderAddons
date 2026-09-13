import bpy
from math import radians, degrees, atan2
from mathutils import Vector, Matrix, Euler
from mathutils.geometry import intersect_line_plane
from bpy_extras import view3d_utils

bl_info = {
    "name": "Lego Track",
    "author": "Joshua Palfrey",
    "version": (1, 0, 0),
    "blender": (5, 2, 0),
    "location": "3dView -> Track",
    "description": ("Track building tool for lego"),
    "category": "Modelling",
}

TRACK_COLLECTION_NAME = "Track"
POINTS_OBJECT_NAME = "TrackPoints"
END_EMPTY_NAME = "End"
 
 
def get_track_collection():
    coll = bpy.data.collections.get(TRACK_COLLECTION_NAME)
    if coll is None:
        coll = bpy.data.collections.new(TRACK_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(coll)
    return coll
 
 
def get_available_angles():
    """Scan the scene for source piece objects (named as plain integers)
    and return their angles, sorted."""
    angles = []
    for obj in bpy.data.objects:
        name = obj.name.split(".")[0]
        try:
            angles.append(int(name))
        except ValueError:
            continue
    return sorted(set(angles))
 
 
def get_end_local(source_obj):
    """Local-space location of the piece's exit connector."""
    for child in source_obj.children:
        if child.name.split(".")[0] == END_EMPTY_NAME:
            return child.matrix_local.translation.copy()
    raise ValueError(
        f"Piece '{source_obj.name}' has no child Empty named "
        f"'{END_EMPTY_NAME}' marking its exit point."
    )
 
 
def normalize_angle(angle_deg):
    """Wrap an angle to the range (-180, 180]. Applied every time heading
    is accumulated so it never drifts to large values (e.g. after many
    pieces / full loops)."""
    a = angle_deg % 360.0
    if a > 180.0:
        a -= 360.0
    return a
 
 
def place_piece(angle, location, heading_deg, collection):
    """Duplicate the source piece for `angle`, place + rotate it to the
    current heading, and return the object plus the world-space
    location/heading the *next* piece should use."""
    source_name = str(angle)
    source = bpy.data.objects.get(source_name)
    if source is None:
        raise ValueError(f"No source object named '{source_name}' found.")
 
    new_obj = source.copy()
    new_obj.data = source.data.copy()
    collection.objects.link(new_obj)
 
    new_obj.location = location
    new_obj.rotation_euler = (0.0, 0.0, radians(heading_deg))
    new_obj.name = f"{source_name}_{len(collection.objects):03d}"
 
    end_local = get_end_local(source)
    rot_mat = Matrix.Rotation(radians(heading_deg), 3, "Z")
    next_location = location + rot_mat @ end_local
    next_heading = normalize_angle(heading_deg + angle)  # <- bug fix: kept bounded
 
    return new_obj, next_location, next_heading
 
 
# --------------------------------------------------------------------------
# Point-cloud companion object
# --------------------------------------------------------------------------
 
def sync_points_object(collection, points):
    """Rebuild the "TrackPoints" mesh from a list of
    {'location': Vector, 'angle': int} dicts - one vertex per placed
    piece, with an "angle" integer attribute on the point domain."""
    obj = bpy.data.objects.get(POINTS_OBJECT_NAME)
    if obj is None:
        mesh = bpy.data.meshes.new(POINTS_OBJECT_NAME)
        obj = bpy.data.objects.new(POINTS_OBJECT_NAME, mesh)
        collection.objects.link(obj)
    else:
        mesh = obj.data
        mesh.clear_geometry()
 
    coords = [p["location"] for p in points]
    mesh.from_pydata(coords, [], [])
    mesh.update()
 
    attr = mesh.attributes.get("angle")
    if attr is None:
        attr = mesh.attributes.new(name="angle", type="INT", domain="POINT")
    attr.data.foreach_set("value", [p["angle"] for p in points])

    heading = mesh.attributes.get("heading")
    if heading is None:
        attr = mesh.attributes.new(name="heading", type="FLOAT", domain="POINT")
    attr.data.foreach_set("value", [p["heading"] for p in points])
    mesh.update()
 
    return obj
 
 
def build_track(angle_sequence, start_location=(0, 0, 0), start_heading=0.0):
    collection = get_track_collection()
    location = Vector(start_location)
    heading = start_heading
    placed = []
    points = []
    for angle in angle_sequence:
        points.append({"location": location.copy(), "angle": angle, "heading": heading})
        obj, location, heading = place_piece(angle, location, heading, collection)
        placed.append(obj)
    sync_points_object(collection, points)
    return placed
 
 
def build_random_track(n, start_location=(0, 0, 0), start_heading=0.0, weights=None):
    angles = get_available_angles()
    sequence = random.choices(angles, weights=weights, k=n)
    return build_track(sequence, start_location, start_heading)
 
 
# --------------------------------------------------------------------------
# Modal "draw" operator
# --------------------------------------------------------------------------
 
class TRACK_OT_draw_modal(bpy.types.Operator):
    """Click to place track pieces; drag to aim, click to confirm each one"""
    bl_idname = "track.draw_modal"
    bl_label = "Draw Track"
    bl_options = {"REGISTER", "UNDO"}
 
    def get_mouse_ground_point(self, context, coord):
        region = context.region
        rv3d = context.region_data
        view_vec = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        plane_co = Vector((0.0, 0.0, self.location.z))
        plane_no = Vector((0.0, 0.0, 1.0))
        return intersect_line_plane(origin, origin + view_vec, plane_co, plane_no)
 
    def update_from_mouse_target(self, context, target):
        vec = self.location - target
        if vec.length < 1e-5:
            return
        raw_heading = degrees(atan2(vec.y, vec.x))  # atan2 is always -180..180
 
        if self.heading is None:
            # First segment: heading is free, always a straight piece
            self.pending_angle = 0
            self.pending_heading = raw_heading
            self.update_preview(context, 0, self.location, raw_heading)
            context.area.header_text_set(
                f"Track: set start heading {raw_heading:.1f}°  (click to confirm)"
            )
        else:
            desired_turn = normalize_angle(raw_heading - self.heading)
            angle = min(
                self.available_angles,
                key=lambda a: abs(normalize_angle(a - desired_turn)),
            )
            self.pending_angle = angle
            self.pending_heading = self.heading
            self.update_preview(context, angle, self.location, self.heading)
            context.area.header_text_set(f"Track: next piece {angle}°  (click to confirm)")
 
    def update_preview(self, context, angle, location, heading_deg):
        source = bpy.data.objects.get(str(angle))
        if source is None:
            return
        if self.preview_obj is None or self.preview_obj.name not in bpy.data.objects:
            self.preview_obj = source.copy()
            self.preview_obj.data = source.data  # shared, not copied - preview only
            self.preview_obj.name = "__track_preview__"
            self.preview_obj.display_type = "WIRE"
            self.preview_obj.hide_select = True
            context.collection.objects.link(self.preview_obj)
        elif self.preview_obj.data is not source.data:
            self.preview_obj.data = source.data
 
        self.preview_obj.location = location
        self.preview_obj.rotation_euler = (0.0, 0.0, radians(heading_deg))
 
    def cleanup_preview(self):
        if self.preview_obj is not None and self.preview_obj.name in bpy.data.objects:
            bpy.data.objects.remove(self.preview_obj, do_unlink=True)
        self.preview_obj = None
 
    def undo_last_piece(self, context):
        entry = self.history.pop()
        obj = bpy.data.objects.get(entry["obj_name"])
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        if self.points:
            self.points.pop()
        sync_points_object(self.collection, self.points)
 
        self.location = entry["prev_location"]
        self.heading = entry["prev_heading"]  # may be None, re-enabling free heading
 
        if self.last_mouse_coord is not None:
            target = self.get_mouse_ground_point(context, self.last_mouse_coord)
            if target is not None:
                self.update_from_mouse_target(context, target)
 
    def invoke(self, context, event):
        self.available_angles = get_available_angles()
        if not self.available_angles:
            self.report({"ERROR"}, "No piece objects found (name them '0', '30', etc.)")
            return {"CANCELLED"}
        if 0 not in self.available_angles:
            self.report({"ERROR"}, "Need a '0' (straight) piece to set the starting heading")
            return {"CANCELLED"}
 
        self.collection = get_track_collection()
        self.location = context.scene.cursor.location.copy()
        self.heading = None  # not yet set - first click establishes it
        self.preview_obj = None
        self.pending_angle = 0
        self.pending_heading = 0.0
        self.last_mouse_coord = None
        self.history = []   # stack of {'obj_name', 'prev_location', 'prev_heading'}
        self.points = []    # list of {'location', 'angle'} for the point cloud
 
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Track: aim + click to place, right-click to undo, ESC to finish")
        return {"RUNNING_MODAL"}
 
    def modal(self, context, event):
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}
 
        if event.type == "MOUSEMOVE":
            coord = (event.mouse_region_x, event.mouse_region_y)
            self.last_mouse_coord = coord
            target = self.get_mouse_ground_point(context, coord)
            if target is not None:
                self.update_from_mouse_target(context, target)
 
        elif event.type == "LEFTMOUSE" and event.value == "PRESS":
            prev_location = self.location.copy()
            prev_heading = self.heading  # None on the very first piece
            try:
                obj, next_loc, next_heading = place_piece(
                    self.pending_angle, self.location, self.pending_heading, self.collection
                )
            except ValueError as e:
                self.report({"ERROR"}, str(e))
                self.cleanup_preview()
                context.area.header_text_set(None)
                return {"CANCELLED"}
 
            self.points.append({"location": self.location.copy(), "angle": self.pending_angle, "heading":prev_heading if prev_heading else 0})
            sync_points_object(self.collection, self.points)
 
            self.history.append({
                "obj_name": obj.name,
                "prev_location": prev_location,
                "prev_heading": prev_heading,
            })
 
            self.location = next_loc
            self.heading = next_heading
 
        elif event.type == "RIGHTMOUSE" and event.value == "PRESS":
            # Right-click steps back one piece; with nothing left to undo
            # it just stops the tool (whatever's already built stays).
            if self.history:
                self.undo_last_piece(context)
                return {"RUNNING_MODAL"}
            else:
                self.cleanup_preview()
                context.area.header_text_set(None)
                return {"CANCELLED"}
 
        elif event.type == "ESC":
            self.cleanup_preview()
            context.area.header_text_set(None)
            return {"CANCELLED"}
 
        elif event.type in {"RET", "NUMPAD_ENTER"}:
            self.cleanup_preview()
            context.area.header_text_set(None)
            return {"FINISHED"}
 
        return {"RUNNING_MODAL"}
 
 
class VIEW3D_PT_track_builder(bpy.types.Panel):
    bl_label = "Track Builder"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Track"
 
    def draw(self, context):
        layout = self.layout
        layout.operator("track.draw_modal", text="Draw Track", icon="GREASEPENCIL")
 
 
classes = (TRACK_OT_draw_modal, VIEW3D_PT_track_builder)
 
 
def register():
    for cls in classes:
        bpy.utils.register_class(cls)
 
 
def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)
