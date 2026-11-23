"""
Builds the LDraw colour asset library used by colourShelf.py.

    blender -b --factory-startup --python legoEditor/external/buildColourLibrary.py

Reads ldraw/col/colMap.json (build it first by importing a brick, or with
colors.getColMap()) and writes, next to this script:

    colourLibrary/ldrawColours.blend        one placeholder asset per colour
    colourLibrary/blender_assets.cats.txt   one catalog per material type

Each placeholder is an empty object with a swatch preview, the colour's name,
tags (code, hex, material) and "ldraw_colour:<code>" in its description.
Swatches are fully opaque (Blender draws a checkerboard behind any alpha) and show
the material instead: transparent = lighter diagonal half, chrome/metal/pearlescent
= sheen, glitter = sparkles, speckle = specks, fabric = weave, glow = bright centre.

Run in the background with a fresh file: it replaces whatever is open.
"""
import json
import uuid
from pathlib import Path

import bpy
import numpy as np

HERE = Path(__file__).resolve().parent
COLMAP_FILE = HERE.parent / "ldraw" / "col" / "colMap.json"
LIBRARY_DIR = HERE 
LIBRARY_FILE = LIBRARY_DIR / "ldrawColours.blend"

DESC_PREFIX = "ldraw_colour:"
SKIP_CODES = {16, 24}      # 16 = inherit the parent's colour, 24 = edge colour
PREVIEW_PX = 128
ICON_PX = 32

# fixed namespace, so catalog ids stay the same every rebuild
CATALOG_NS = uuid.UUID("6d1c3a0e-2f4b-4c8e-9a51-1b2d7f0c9e44")

# finish from colMap.json -> catalog name, in tab order
FINISH_CATALOGS = {
    "SOLID": "Solid",
    "CHROME": "Chrome",
    "METAL": "Metallic",
    "PEARLESCENT": "Pearlescent",
    "RUBBER": "Rubber",
    "GLITTER": "Glitter",
    "SPECKLE": "Speckle",
    "FABRIC": "Fabric",
}
CATALOG_ORDER = ["Solid", "Transparent", "Chrome", "Metallic", "Pearlescent",
                 "Rubber", "Glitter", "Speckle", "Fabric", "Glow"]


# ---------------------------------------------------------------------------
# Colour info
# ---------------------------------------------------------------------------

def hexToRgb(hexVal):
    h = hexVal.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

def isTrans(entry):
    return entry.get("alpha", 255) < 255

def isGlow(entry):
    return entry.get("luminance", 0) > 0

def catalogFor(entry):
    # The finish wins over transparency, so Trans Glitter is in Glitter and Trans
    # Rubber in Rubber (both still tagged "Transparent"); plain see-through
    # colours go in Transparent.
    finish = entry.get("finish", "SOLID").upper()
    if isGlow(entry):
        return "Glow"
    if finish == "SOLID":
        return "Transparent" if isTrans(entry) else "Solid"
    return FINISH_CATALOGS.get(finish, finish.title())

def catalogId(name):
    return str(uuid.uuid5(CATALOG_NS, name))


# ---------------------------------------------------------------------------
# Swatch images
# ---------------------------------------------------------------------------

def swatch(rgb, finish, trans, glow, n, seed):
    """n x n RGBA float image (row 0 at the bottom), alpha always 1."""
    col = np.empty((n, n, 3), np.float32)
    col[:] = rgb
    y, x = np.mgrid[0:n, 0:n].astype(np.float32) / (n - 1)
    rng = np.random.default_rng(seed)
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]

    def mix(mask, target, amount):
        col[mask] = col[mask] * (1 - amount) + np.asarray(target, np.float32) * amount

    def dots(density, cell):
        # random square specks on a coarse grid, so they read at small sizes
        g = -(-n // cell)
        m = rng.random((g, g)) < density
        return np.kron(m, np.ones((cell, cell), bool))[:n, :n]

    if finish in ("CHROME", "METAL", "PEARLESCENT"):
        strength = {"CHROME": 0.75, "METAL": 0.5, "PEARLESCENT": 0.35}[finish]
        band = np.exp(-((x - (1 - y)) * 2.5) ** 2)          # diagonal sheen
        col *= (0.7 + 0.3 * y)[..., None]                    # darker at the bottom
        col += (1 - col) * (strength * band)[..., None]
    elif finish == "GLITTER":
        mix(dots(0.10, max(2, n // 24)), (1, 1, 1), 0.85)
    elif finish == "SPECKLE":
        speck = (0.75, 0.75, 0.75) if lum < 0.35 else (0, 0, 0)
        mix(dots(0.12, max(2, n // 24)), speck, 0.6)
    elif finish == "FABRIC":
        c = max(2, n // 16)
        ix, iy = np.mgrid[0:n, 0:n]
        col[((ix // c + iy // c) % 2).astype(bool)] *= 0.82
    elif finish == "RUBBER":
        col *= 0.92

    if glow:
        r = np.sqrt((x - 0.5) ** 2 + (y - 0.5) ** 2)
        col += (1 - col) * (0.6 * np.clip(1 - r * 2, 0, 1))[..., None]

    if trans:
        # top-right half washed out: towards white, or towards grey for light colours
        wash = (0.92, 0.92, 0.92) if lum < 0.7 else (0.55, 0.55, 0.55)
        mix(x + y > 1.0, wash, 0.55)

    img = np.ones((n, n, 4), np.float32)
    img[..., :3] = np.clip(col, 0, 1)
    return img


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def writeCatalogs(names):
    lines = ["# This is an Asset Catalog Definition file for Blender.",
             "# Written by buildColourLibrary.py, rebuilt every time.",
             "",
             "VERSION 1",
             ""]
    ordered = sorted(names, key=lambda n: (CATALOG_ORDER.index(n) if n in CATALOG_ORDER else 99, n))
    for name in ordered:
        lines.append(f"{catalogId(name)}:{name}:{name}")
    (LIBRARY_DIR / "blender_assets.cats.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

def makeAsset(code, entry, seed):
    name = entry["name"].replace("_", " ")
    hexVal = entry["col"].lstrip("#").upper()
    finish = entry.get("finish", "SOLID").upper()
    cat = catalogFor(entry)

    obj = bpy.data.objects.new(f"{name} ({code})", None)
    obj.asset_mark()                         # also gives it a fake user, so it's saved
    data = obj.asset_data
    data.description = f"{DESC_PREFIX}{code}"
    data.catalog_id = catalogId(cat)
    tags = [str(code), hexVal, cat, FINISH_CATALOGS.get(finish, finish.title())]
    if isTrans(entry):
        tags.append("Transparent")
    if isGlow(entry):
        tags.append("Glow")
    for tag in dict.fromkeys(tags):
        data.tags.new(tag, skip_if_exists=True)

    img = swatch(hexToRgb(entry["col"]), finish, isTrans(entry), isGlow(entry), PREVIEW_PX, seed)
    prev = obj.preview_ensure()
    prev.image_size = (PREVIEW_PX, PREVIEW_PX)
    prev.image_pixels_float.foreach_set(img.ravel())
    step = PREVIEW_PX // ICON_PX
    prev.icon_size = (ICON_PX, ICON_PX)
    prev.icon_pixels_float.foreach_set(np.ascontiguousarray(img[::step, ::step]).ravel())
    return cat

def main():
    if not COLMAP_FILE.exists():
        raise SystemExit(f"COLOUR LIBRARY: ERROR, no colour map at {COLMAP_FILE}")
    cMap = json.loads(COLMAP_FILE.read_text(encoding="utf-8"))

    bpy.ops.wm.read_homefile(use_empty=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

    catalogs = set()
    count = 0
    for i, (codeStr, entry) in enumerate(sorted(cMap.items(), key=lambda kv: int(kv[0]))):
        code = int(codeStr)
        if code in SKIP_CODES:
            continue
        catalogs.add(makeAsset(code, entry, seed=i))
        count += 1

    writeCatalogs(catalogs)
    bpy.ops.wm.save_as_mainfile(filepath=str(LIBRARY_FILE), compress=False)
    print(f"COLOUR LIBRARY: wrote {count} colours in {len(catalogs)} catalogs to {LIBRARY_FILE}")

if __name__ == "__main__":
    main()
