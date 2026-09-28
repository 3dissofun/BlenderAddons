# Brick browser: searches <pieceLibrary>/parts.lst (id + description) and shows matching
# bricks as a thumbnail grid using the jpgs in <pieceLibrary>/previews.
# Picking a thumbnail fills in wm.brick_id, so the existing Import Brick operator just works.

import os
import bpy
import bpy.utils.previews
from bpy.props import StringProperty, EnumProperty

MAX_ITEMS = 300          # cap the grid so a broad search doesn't build thousands of icons at once
IMAGE_EXTS = (".jpg", ".jpeg", ".png")
NO_PREVIEW_ICON = 'QUESTION'   # shown for parts in parts.lst that have no jpg

_pcoll = None            # the preview collection (owns the icon ids)
_libraryDir = None       # the library currently indexed
_previewDir = None
_parts = []              # [(brickId, description, searchText)] in parts.lst order
_descriptions = {}       # brickId -> description
_images = {}             # brickId -> image filename in previews/
_items = []              # Blender needs a live Python reference to dynamic enum items, or it can crash


def getLibraryDir():
    prefs = bpy.context.preferences.addons[__package__].preferences
    return bpy.path.abspath(prefs.pieceLibrary)


def readPartsList(path):
    # parts.lst lines look like: "3001.dat                   Brick  2 x  4"
    parts = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            bits = line.split(None, 1)
            if not bits:
                continue
            brickId = bits[0]
            if brickId.lower().endswith(".dat"):
                brickId = brickId[:-4]
            # squash LDraw's padded spacing ("2 x  4" -> "2 x 4") and drop the ~ / = markers
            desc = " ".join(bits[1].split()) if len(bits) > 1 else ""
            desc = desc.lstrip("~=_ ")
            parts.append((brickId, desc, f"{brickId} {desc}".lower()))
    return parts


def scan(force=False):
    # index parts.lst and the previews folder; only re-reads the disk if the library path changed
    global _libraryDir, _previewDir, _parts, _descriptions, _images, _cache
    lib = getLibraryDir()
    if lib == _libraryDir and not force:
        return
    _libraryDir = lib
    _cache = (None, None, [])
    _previewDir = os.path.join(lib, "previews")
    _pcoll.clear()

    _images = {}
    if os.path.isdir(_previewDir):
        for f in os.listdir(_previewDir):
            stem, ext = os.path.splitext(f)
            if ext.lower() in IMAGE_EXTS:
                _images[stem] = f

    listPath = os.path.join(lib, "parts.lst")
    if os.path.isfile(listPath):
        _parts = readPartsList(listPath)
    else:
        # no parts.lst: fall back to just the preview names so the browser still works
        _parts = [(n, "", n.lower()) for n in sorted(_images)]
    _descriptions = {p[0]: p[1] for p in _parts}


def getIcon(brickId):
    # images load lazily, only when a thumbnail is actually needed
    if brickId not in _images:
        return None
    if brickId not in _pcoll:
        _pcoll.load(brickId, os.path.join(_previewDir, _images[brickId]), 'IMAGE')
    return _pcoll[brickId].icon_id


def wordMatches(word, tokens, brickId):
    if brickId.startswith(word):      # "3001" finds 3001, 3001b, 3001d01...
        return True
    if word.replace(".", "").isdigit():
        return word in tokens         # numbers must match whole: "2" shouldn't hit "24"
    return any(t.startswith(word) for t in tokens)   # words match on prefix: "plat" -> "plate"


def search(text):
    # every word typed must match the id or description, in any order,
    # e.g. "plate 2 x 4", "3001", "slope 45", "hinge". Best matches are listed first:
    # exact id, id prefix, description starting with the query, containing it, then the rest.
    words = text.lower().split()
    if not words:
        return list(range(len(_parts)))
    query = " ".join(words)
    buckets = ([], [], [], [], [])
    for i, (brickId, desc, hay) in enumerate(_parts):
        low = brickId.lower()
        tokens = hay.split()
        if not all(wordMatches(w, tokens, low) for w in words):
            continue
        d = desc.lower()
        if low == query:
            buckets[0].append(i)
        elif low.startswith(query):
            buckets[1].append(i)
        elif d.startswith(query):
            buckets[2].append(i)
        elif f" {query}" in f" {d}":
            buckets[3].append(i)
        else:
            buckets[4].append(i)
    # within each group, shorter descriptions first so plain "Plate 2 x 4" beats its variants
    return [i for b in buckets for i in sorted(b, key=lambda i: len(_parts[i][1]))]


_cache = (None, None, [])    # (libraryDir, searchText, items) - Blender asks for items on every redraw


def cachedItems(text):
    global _cache
    if _cache[0] == _libraryDir and _cache[1] == text:
        return _cache[2]
    items = []
    for i in search(text)[:MAX_ITEMS]:
        brickId, desc, _ = _parts[i]
        icon = getIcon(brickId)
        # the index in _parts is a stable number, so the selection survives changing the search
        items.append((brickId, brickId, desc, icon if icon is not None else NO_PREVIEW_ICON, i))
    _cache = (_libraryDir, text, items)
    return items


def brickItems(self, context):
    global _items
    if _pcoll is None:
        return []
    scan()
    _items = cachedItems(context.window_manager.brick_search)
    return _items


def onBrickPicked(self, context):
    self.brick_id = self.brick_browser


def describe(brickId):
    # description from parts.lst, or "" if unknown
    scan()
    return _descriptions.get(brickId, "")




def allBrickNames():
    # handy elsewhere, e.g. picking a random brick: random.choice(browser.allBrickNames())
    scan()
    return [p[0] for p in _parts]


class LE_OT_RefreshPreviews(bpy.types.Operator):
    bl_idname = "lego.refresh_previews"
    bl_label = "Refresh Previews"
    bl_description = "Re-read parts.lst and the previews folder in the piece library"

    def execute(self, context):
        scan(force=True)
        self.report({'INFO'}, f"{len(_parts)} parts, {len(_images)} previews found")
        return {'FINISHED'}


classes = (LE_OT_RefreshPreviews,)


def register():
    global _pcoll
    _pcoll = bpy.utils.previews.new()
    bpy.types.WindowManager.brick_search = StringProperty(
        name="Search", description="Filter bricks by id or description, e.g. 'plate 2 x 4'", default="")
    bpy.types.WindowManager.brick_browser = EnumProperty(
        name="Brick", items=brickItems, update=onBrickPicked)
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    global _pcoll, _libraryDir, _items, _cache
    _cache = (None, None, [])
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.WindowManager.brick_browser
    del bpy.types.WindowManager.brick_search
    if _pcoll is not None:
        bpy.utils.previews.remove(_pcoll)
    _pcoll, _libraryDir, _items = None, None, []
