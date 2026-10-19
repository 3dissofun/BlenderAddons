"""Basic LXFML (LEGO Digital Designer) -> LDraw .ldr converter.

Uses the community LDraw.xml mapping file for part numbers and origin offsets.

Pipeline per part:
    LDD world matrix  @  part origin fix (LDraw.xml <Transformation>)
    -> LDD space (Y up, 0.8 units per stud) to LDraw space (Y down, 20 LDU per stud)
    -> one "1 ..." line in the .ldr

Assumptions worth checking against a known model (see the constants below):
  * the 12 numbers in a Bone's transformation are  a b c  d e f  g h i  x y z,
    where each triple is the image of one basis vector (row-vector convention)
  * a <Transformation> maps the LDD part frame to the LDraw part frame
    (flip PART_FIX_INVERSE if parts end up offset/rotated about their own origin)
"""
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

LDD_TO_LDU = 25.0                    # 0.8 LDD units per stud -> 20 LDU per stud
FLIP = np.diag([1.0, -1.0, -1.0])    # Y-up -> Y-down; a proper rotation, so nothing mirrors
PART_FIX_INVERSE = True              # <Transformation> maps LDD local -> LDraw local, so a placement needs its inverse
LDRAW_XML = Path(__file__).with_name("ldraw.xml")  # default mapping file, override as needed
FALLBACK_COLOUR = 7                 # LDraw light grey, for LDD colours missing from LDraw.xml

# ---------------------------------------------------------------------------
# Mapping file
# ---------------------------------------------------------------------------

def _num(node, key, default=0.0):
    try:
        return float(node.get(key, default))
    except ValueError:
        return default  # the community file has the odd typo (tz="°")

def axisAngle(axis, angle):
    # Rodrigues: axis-angle (radians) -> 3x3 rotation
    axis = np.asarray(axis, dtype=np.float64)
    n = np.linalg.norm(axis)
    if n < 1e-9 or angle == 0:
        return np.eye(3)
    x, y, z = axis / n
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + np.sin(angle) * k + (1 - np.cos(angle)) * (k @ k)

def loadMapping(ldrawXml):
    # Returns (bricks, colours, fixes):
    #   bricks:  LDD design id (str) -> LDraw file name
    #   colours: LDD colour id (int) -> LDraw colour code (int)
    #   fixes:   LDraw file name (lowercase) -> 4x4 part origin fix, in LDD units
    # LDraw.xml says that for export (LDD -> LDraw) the LAST entry with a given
    # lego id wins, which is how the rubber/chrome variants near the top of the
    # colour section get overridden by the official colours further down.
    root = ET.parse(str(ldrawXml)).getroot()

    bricks = {}
    for b in root.iter("Brick"):
        lego, ldraw = b.get("lego"), b.get("ldraw")
        if lego and ldraw:
            bricks[lego.strip()] = ldraw.strip()

    colours = {}
    for c in root.iter("Material"):
        try:
            colours[int(c.get("lego"))] = int(c.get("ldraw"))
        except (TypeError, ValueError):
            continue

    fixes = {}
    for t in root.iter("Transformation"):
        name = (t.get("ldraw") or "").strip().lower()
        if not name:
            continue
        m = np.eye(4)
        m[:3, :3] = axisAngle((_num(t, "ax"), _num(t, "ay"), _num(t, "az")), _num(t, "angle"))
        m[:3, 3] = (_num(t, "tx"), _num(t, "ty"), _num(t, "tz"))
        fixes[name] = np.linalg.inv(m) if PART_FIX_INVERSE else m
    return bricks, colours, fixes

# ---------------------------------------------------------------------------
# LXFML
# ---------------------------------------------------------------------------

def _readLxfml(path):
    path = Path(path)
    if path.suffix.lower() == ".lxf":  # .lxf is a zip holding IMAGE100.LXFML
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".lxfml")), None)
            if name is None:
                raise ValueError(f"No .lxfml inside '{path.name}'")
            return ET.fromstring(z.read(name))
    return ET.parse(str(path)).getroot()

def readParts(path):
    # Yields (design id, LDD colour id, 4x4 world matrix in LDD units, column vectors)
    root = _readLxfml(path)
    bricks = root.find("Bricks")
    if bricks is None:
        return
    for brick in bricks.findall("Brick"):
        for part in brick.findall("Part"):
            design = part.get("designID") or brick.get("designID")
            if design:
                # some exporters write "3068;T": a part number plus a ";letter" suffix.
                # LDraw.xml only knows the bare number.
                design = design.split(";")[0].strip()
            # "materials" is a comma list, one entry per material slot. Newer files
            # write each as "colour:decoration" ("337:0"), older ones "colour,decoration"
            # ("21,0"). Either way the first number of the first entry is the colour.
            colour = int((part.get("materials") or "0").split(",")[0].split(":")[0])
            bones = part.findall("Bone")
            if not design or not bones:
                continue
            if len(bones) > 1:
                print(f"LXFML: NOTE, part {design} has {len(bones)} bones, using the first")
            v = [float(s) for s in bones[0].get("transformation").split(",")]
            m = np.eye(4)
            m[:3, :3] = np.array(v[:9]).reshape(3, 3).T  # triples are basis images -> columns
            m[:3, 3] = v[9:12]
            yield design, colour, m

# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def _line(colour, m, partName):
    vals = [m[0, 3], m[1, 3], m[2, 3]] + list(m[:3, :3].ravel())
    nums = " ".join(f"{(0.0 if abs(v) < 5e-7 else v):.6g}" for v in vals)
    return f"1 {colour} {nums} {partName}"

def lddToLdr(pathToXmlFile, pathToLdrOutput, ldrawXml=None):
    # pathToXmlFile:   the model to convert (.lxfml, or .lxf which is zipped)
    # pathToLdrOutput: where to write the .ldr
    # ldrawXml:        mapping file; defaults to LDRAW_XML (set it once, or put an
    #                  ldraw.xml next to this module)
    # Returns the output Path
    lxfmlPath = Path(pathToXmlFile)
    outPath = Path(pathToLdrOutput)
    ldrawXml = ldrawXml or LDRAW_XML
    if not Path(ldrawXml).is_file():
        raise FileNotFoundError(f"LDraw.xml mapping file not found: '{ldrawXml}'")
    bricks, colours, fixes = loadMapping(ldrawXml)

    lines = [f"0 {lxfmlPath.stem}", "0 Name: " + outPath.name, "0 Author: lxfml converter", ""]
    missing, badColours = set(), set()

    for design, lddColour, world in readParts(lxfmlPath):
        # LDraw.xml: a <Brick> entry isn't needed for exact "XXX" <-> "XXX.dat"
        # matches, the <Transformation> alone is enough
        partName = (bricks.get(design) or f"{design}.dat").lower()
        if design not in bricks and partName not in fixes:
            missing.add(design)  # no mapping of any kind; keep the guess

        m = world @ fixes.get(partName, np.eye(4))
        out = np.eye(4)
        # Conjugate by the Y-up -> Y-down flip: it applies to the world *and* to the
        # part's own local axes, so the rotation gets FLIP on both sides.
        # (Left side only leaves every part upside down and mirrors 90 degree turns.)
        out[:3, :3] = FLIP @ m[:3, :3] @ FLIP
        out[:3, 3] = FLIP @ m[:3, 3] * LDD_TO_LDU

        colour = colours.get(lddColour)
        if colour is None:
            badColours.add(lddColour)
            colour = FALLBACK_COLOUR
        lines.append(_line(colour, out, partName))

    outPath.parent.mkdir(parents=True, exist_ok=True)
    outPath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if missing:
        print(f"LXFML: WARNING, no mapping for LDD parts: {sorted(missing)}")
    if badColours:
        print(f"LXFML: WARNING, unmapped LDD colours (used {FALLBACK_COLOUR}): {sorted(badColours)}")
    return outPath
