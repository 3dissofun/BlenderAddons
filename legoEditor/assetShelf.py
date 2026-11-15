"""
Brick browser for the Lego Editor: custom search + preview grid.

Replaces the asset shelf. No asset library is needed: it reads parts.json and
previews/ written by external/extractCatalog.py, from the folder set in
Preferences > Add-ons > Lego Editor > Brick Catalog (default: external/catalog).

    Ctrl+Shift+A (3D view)  search popup at the mouse
    Sidebar > Lego tab       the same browser, docked

    Type to search: part number ("3001"), name words ("slope 45"), sizes
    ("2x4" matches "2 x 4" and "4 x 2") and tags. Empty search shows recent bricks.

    Click a brick   -> it follows the mouse in the viewport, click to place
                       R / wheel = rotate 90°, Ctrl = no grid, Esc / right-click = cancel
    Right-click     -> Add at 3D Cursor, Show Variants, Copy Part Number

Previews are loaded with bpy.utils.previews only for bricks that are shown,
then cached for the session.
"""

import json
import math
import os
import re
from pathlib import Path

import bpy
import bpy.utils.previews
from bpy.props import IntProperty, StringProperty
from bpy_extras import view3d_utils
from mathutils import Matrix, Vector, geometry

from . import utils
from .ldraw.importer.importer import getBrickMesh, getBrickObj

DEFAULT_CATALOG = Path(__file__).parent / "external" / "catalog"
HIDE_CATEGORIES = {"Moved", "Sticker Shortcut"}
HIDE_PREFIXES = ("~", "=", "_")   # helper/moved, alias, physical colour duplicates

PER_PAGE = 20            # results per page
POPUP_COLUMNS = 4
TILE_SCALE = 5.0         # preview size, in icon units (~20px each)
MAX_RESULTS = 400        # results kept after ranking
MAX_RECENT = 20

GRID = 20.0              # stud pitch, LDU (1 Blender unit = 1 LDU)
PLATE = 8.0              # plate height, LDU

KEYMAP = dict(type='A', value='PRESS', ctrl=True, shift=True)

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)(?:\s*x\s*(\d+(?:\.\d+)?))?")
_PRINTED_RE = re.compile(r"(p[0-9a-z]{2,}|pat\d+|c\d{2})$")
_BASE_RE = re.compile(r"^(\d+[a-z]?)")


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class Part:
    __slots__ = ("file", "stem", "name", "category", "image", "printed",
                 "name_key", "hay")

    def __init__(self, file, data, root):
        self.file = file
        self.stem = os.path.splitext(file)[0].lower()
        self.name = data["name"]
        self.category = data.get("category") or ""
        img = data.get("image")
        self.image = str(root / img) if img else None
        self.printed = bool(_PRINTED_RE.search(self.stem)) or "pattern" in self.name.lower()

        # Leading spaces let " token" test for a word that starts with token,
        # so "2x4" doesn't match inside "12x4" but "tech" still finds "technic"
        sizes = " ".join(size_variants(self.name))
        self.name_key = f" {normalise(self.name)} {sizes}"
        tags = " ".join(normalise(t) for t in data.get("tags", []))
        self.hay = f"{self.name_key} {self.stem} {normalise(self.category)} {tags}"


def normalise(text):
    """Lowercase, with sizes squashed: 'Brick 2 x 4' -> 'brick 2x4'."""
    text = text.lower()
    return _SIZE_RE.sub(lambda m: "x".join(g for g in m.groups() if g), text)


def size_variants(name):
    """Both orders of each size, so '4x2' finds 'Brick 2 x 4'."""
    out = []
    for a, b, c in _SIZE_RE.findall(name):
        out += [f"{a}x{b}", f"{b}x{a}"]
        if c:
            out += [f"{a}x{b}x{c}", f"{b}x{a}x{c}"]
    return out


class Catalog:
    def __init__(self):
        self.root = None
        self.parts = []
        self.by_file = {}
        self.error = ""

    def path(self):
        pref = utils.getPref("brickCatalog")
        return Path(bpy.path.abspath(pref)) if pref else DEFAULT_CATALOG

    def ensure(self):
        """(Re)load parts.json if the folder changed. Returns True if usable."""
        root = self.path()
        if root == self.root:
            return bool(self.parts)
        self.root, self.parts, self.by_file, self.error = root, [], {}, ""
        previews.clear()
        try:
            with open(root / "parts.json", encoding="utf-8") as f:
                data = json.load(f)
        except OSError:
            self.error = f"No parts.json in {root}"
            return False
        for file, d in data.items():
            if d["name"].startswith(HIDE_PREFIXES) or d.get("category") in HIDE_CATEGORIES:
                continue
            p = Part(file, d, root)
            self.parts.append(p)
            self.by_file[file] = p
        self.parts.sort(key=lambda p: p.name.lower())
        return True


catalog = Catalog()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(query):
    """Ranked list of Parts for a query string."""
    q = normalise(query.strip())
    tokens = q.split()
    if not tokens:
        return []
    words = [" " + t for t in tokens]           # match from the start of a word
    scored = []
    for p in catalog.parts:
        if p.stem == q:
            score = 1000
        elif p.stem.startswith(q):
            score = 600 - len(p.stem)
        elif all(t in p.name_key for t in words):
            score = 300
            if p.name_key.startswith(words[0]):
                score += 40
            score -= len(p.name) * 0.5          # shorter, plainer names first
        elif all(t in p.hay for t in words):
            score = 100 - len(p.name) * 0.2
        else:
            continue
        if p.printed:
            score -= 80                          # base parts before printed variants
        scored.append((score, p))
    scored.sort(key=lambda sp: (-sp[0], sp[1].name.lower()))
    return [p for _, p in scored[:MAX_RESULTS]]


class State:
    results = []        # current search results (Parts)
    recent = []         # files placed this session, newest first


def current_list(context):
    wm = context.window_manager
    if wm.lego_brick_search.strip():
        return State.results
    return [catalog.by_file[f] for f in State.recent if f in catalog.by_file]


def on_search(self, context):
    catalog.ensure()
    State.results = search(self.lego_brick_search)
    self.lego_brick_page = 0


def remember(file):
    if file in State.recent:
        State.recent.remove(file)
    State.recent.insert(0, file)
    del State.recent[MAX_RECENT:]


# ---------------------------------------------------------------------------
# Previews
# ---------------------------------------------------------------------------

class Previews:
    def __init__(self):
        self.coll = None

    def icon(self, part):
        if not part.image or not os.path.isfile(part.image):
            return 0
        if self.coll is None:
            self.coll = bpy.utils.previews.new()
        item = self.coll.get(part.stem)
        if item is None:
            item = self.coll.load(part.stem, part.image, 'IMAGE')
        return item.icon_id

    def clear(self):
        if self.coll is not None:
            bpy.utils.previews.remove(self.coll)
            self.coll = None


previews = Previews()


# ---------------------------------------------------------------------------
# Drawing (shared by the popup and the sidebar)
# ---------------------------------------------------------------------------

def draw_browser(layout, context, columns=0, focus=False):
    wm = context.window_manager
    row = layout.row()
    if focus:
        row.activate_init = True                 # typing goes straight into the field
    row.prop(wm, "lego_brick_search", text="", icon='VIEWZOOM')

    if not catalog.ensure():
        layout.label(text=catalog.error or "Brick catalog not loaded", icon='ERROR')
        return

    items = current_list(context)
    if not items:
        layout.label(text="No matches" if wm.lego_brick_search.strip()
                     else "Type to search bricks")
        return

    pages = max(1, math.ceil(len(items) / PER_PAGE))
    page = min(wm.lego_brick_page, pages - 1)
    shown = items[page * PER_PAGE:(page + 1) * PER_PAGE]

    grid = layout.grid_flow(row_major=True, columns=columns,
                            even_columns=True, even_rows=True, align=False)
    for p in shown:
        col = grid.column(align=True)
        icon = previews.icon(p)
        if icon:
            col.template_icon(icon_value=icon, scale=TILE_SCALE)
        op = col.operator("lego.place_brick", text=p.stem)
        op.file = p.file

    row = layout.row(align=True)
    sub = row.row(align=True)
    sub.enabled = page > 0
    sub.operator("lego.brick_page", text="", icon='TRIA_LEFT').delta = -1
    title = f"{len(items)} results" if wm.lego_brick_search.strip() else "Recent"
    row.label(text=f"{title} · page {page + 1}/{pages}")
    sub = row.row(align=True)
    sub.enabled = page < pages - 1
    sub.operator("lego.brick_page", text="", icon='TRIA_RIGHT').delta = 1


class LEGO_PT_brick_browser(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "LEGO"
    bl_label = "Bricks"

    def draw(self, context):
        draw_browser(self.layout, context)


class LEGO_OT_brick_browser(bpy.types.Operator):
    """Search bricks and place one"""
    bl_idname = "lego.brick_browser"
    bl_label = "Add Brick"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        width = int(POPUP_COLUMNS * TILE_SCALE * 22 * context.preferences.system.ui_scale)
        return context.window_manager.invoke_popup(self, width=width)

    def draw(self, context):
        draw_browser(self.layout, context, columns=POPUP_COLUMNS, focus=True)

    def execute(self, context):
        return {'FINISHED'}


class LEGO_OT_brick_page(bpy.types.Operator):
    """Next / previous page of results"""
    bl_idname = "lego.brick_page"
    bl_label = "Change Page"
    bl_options = {'INTERNAL'}

    delta: IntProperty()

    def execute(self, context):
        wm = context.window_manager
        wm.lego_brick_page = max(0, wm.lego_brick_page + self.delta)
        return {'FINISHED'}


class LEGO_OT_brick_search_set(bpy.types.Operator):
    """Search for this text"""
    bl_idname = "lego.brick_search_set"
    bl_label = "Set Brick Search"
    bl_options = {'INTERNAL'}

    query: StringProperty()

    def execute(self, context):
        context.window_manager.lego_brick_search = self.query   # runs on_search
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Placing bricks
# ---------------------------------------------------------------------------

def new_brick(context, file):
    """Get the brick's mesh and wrap it in a new, selected object."""
    mesh = getBrickMesh(file)
    if mesh is None:
        return None
    obj = getBrickObj(mesh)
    context.collection.objects.link(obj)
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    remember(file)
    return obj


def part_tooltip(file):
    p = catalog.by_file.get(file)
    return f"{p.name}\n{file} · {p.category}" if p else file


class LEGO_OT_add_brick(bpy.types.Operator):
    """Add this brick at the 3D cursor"""
    bl_idname = "lego.add_brick"
    bl_label = "Add Brick at Cursor"
    bl_options = {'REGISTER', 'UNDO'}

    file: StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        return part_tooltip(props.file)

    def execute(self, context):
        obj = new_brick(context, self.file)
        if obj is None:
            self.report({'WARNING'}, f"Couldn't build {self.file}")
            return {'CANCELLED'}
        obj.location = context.scene.cursor.location
        return {'FINISHED'}


class LEGO_OT_place_brick(bpy.types.Operator):
    """Pick up this brick and click in the viewport to place it"""
    bl_idname = "lego.place_brick"
    bl_label = "Place Brick"
    bl_options = {'REGISTER', 'UNDO'}

    file: StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        return part_tooltip(props.file) + "\nClick, then click in the viewport to place"

    def execute(self, context):
        # Non-interactive (scripts, redo): same as Add at Cursor
        return bpy.ops.lego.add_brick(file=self.file)

    def invoke(self, context, event):
        area = context.area
        if area is None or area.type != 'VIEW_3D':
            return self.execute(context)
        self.region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        self.rv3d = area.spaces.active.region_3d

        self.obj = new_brick(context, self.file)
        if self.obj is None:
            self.report({'WARNING'}, f"Couldn't build {self.file}")
            return {'CANCELLED'}

        self.rot = 0
        self.lift = -min(Vector(c).z for c in self.obj.bound_box)   # sit on surfaces
        self.obj.location = context.scene.cursor.location
        if self.over_view(event):
            self.update(context, event)

        context.window_manager.modal_handler_add(self)
        context.window.cursor_modal_set('HAND')
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            if self.over_view(event):
                self.update(context, event)

        elif event.type in {'R', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} and event.value == 'PRESS':
            self.rot += -1 if event.type == 'WHEELDOWNMOUSE' else 1
            self.update(context, event)

        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if self.over_view(event):
                context.window.cursor_modal_restore()
                return {'FINISHED'}
            return self.cancel_place(context)

        elif event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self.cancel_place(context)

        return {'RUNNING_MODAL'}

    def cancel_place(self, context):
        context.window.cursor_modal_restore()
        bpy.data.objects.remove(self.obj, do_unlink=True)
        return {'CANCELLED'}

    def over_view(self, event):
        r = self.region
        return (r is not None and r.x <= event.mouse_x < r.x + r.width
                and r.y <= event.mouse_y < r.y + r.height)

    def update(self, context, event):
        hit = self.surface_point(context, event)
        if hit is None:
            return
        if not event.ctrl:
            hit = Vector((round(hit.x / GRID) * GRID,
                          round(hit.y / GRID) * GRID,
                          round(hit.z / PLATE) * PLATE))
        self.obj.matrix_world = (Matrix.Translation(hit + Vector((0, 0, self.lift)))
                                 @ Matrix.Rotation(self.rot * math.pi / 2, 4, 'Z'))

    def surface_point(self, context, event):
        """Scene point under the mouse (ignoring this brick), else ground z=0."""
        coord = (event.mouse_x - self.region.x, event.mouse_y - self.region.y)
        origin = view3d_utils.region_2d_to_origin_3d(self.region, self.rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(self.region, self.rv3d, coord)
        depsgraph = context.evaluated_depsgraph_get()
        start = origin
        for _ in range(16):
            ok, loc, _n, _i, hit_obj, _m = context.scene.ray_cast(depsgraph, start, direction)
            if not ok:
                break
            if hit_obj.original != self.obj:
                return loc
            start = loc + direction * 1e-3
        return geometry.intersect_line_plane(
            origin, origin + direction, Vector((0, 0, 0)), Vector((0, 0, 1)))


# ---------------------------------------------------------------------------
# Right-click menu on brick buttons
# ---------------------------------------------------------------------------

def button_context_menu(self, context):
    op = getattr(context, "button_operator", None)
    if op is None or op.bl_rna.identifier != LEGO_OT_place_brick.bl_rna.identifier:
        return
    file = op.file
    stem = os.path.splitext(file)[0]
    base = _BASE_RE.match(stem)
    layout = self.layout
    layout.separator()
    layout.operator("lego.add_brick", text="Add at 3D Cursor", icon='PIVOT_CURSOR').file = file
    if base:
        layout.operator("lego.brick_search_set", text="Show Variants",
                        icon='VIEWZOOM').query = base.group(1)
    layout.operator("lego.copy_part_number", icon='COPYDOWN').text = stem


class LEGO_OT_copy_part_number(bpy.types.Operator):
    """Copy the part number to the clipboard"""
    bl_idname = "lego.copy_part_number"
    bl_label = "Copy Part Number"
    bl_options = {'INTERNAL'}

    text: StringProperty()

    def execute(self, context):
        context.window_manager.clipboard = self.text
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    LEGO_OT_add_brick,
    LEGO_OT_place_brick,
    LEGO_OT_brick_page,
    LEGO_OT_brick_search_set,
    LEGO_OT_copy_part_number,
    LEGO_OT_brick_browser,
    LEGO_PT_brick_browser,
)

addon_keymaps = []


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.lego_brick_search = StringProperty(
        name="Search Bricks", options={'TEXTEDIT_UPDATE'}, update=on_search,
        description="Part number, name words, size (2x4) or tag")
    bpy.types.WindowManager.lego_brick_page = IntProperty(min=0)
    menu = getattr(bpy.types, "WM_MT_button_context", None)   # defined by Blender's UI scripts
    if menu:
        menu.append(button_context_menu)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="3D View", space_type='VIEW_3D')
        kmi = km.keymap_items.new(LEGO_OT_brick_browser.bl_idname, **KEYMAP)
        addon_keymaps.append((km, kmi))


def unregister():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    menu = getattr(bpy.types, "WM_MT_button_context", None)
    if menu:
        menu.remove(button_context_menu)
    del bpy.types.WindowManager.lego_brick_page
    del bpy.types.WindowManager.lego_brick_search
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    previews.clear()
    catalog.root = None
