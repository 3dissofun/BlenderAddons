from pathlib import Path
import json

from ..importer import library

FINISH_KEYWORDS = ("CHROME", "PEARLESCENT", "METAL", "RUBBER")

colMap = None # In memory representation of parsed data

def parseCol(rawCode):
    try:
        colCode = int(rawCode)
    except ValueError:
        print(f"LDRAW PARSER: WARNING, Direct colors not implemented, skipping '{rawCode}', using 16")
        colCode = 16
    return colCode

def buildColMap():
    global colMap
    partLib = library.getPartLibrary()
    if not partLib:
        print("LDRAW COLMAP: ERROR, No part library set")
        return False
 
    ldConfigFile = Path(partLib) / "LDConfig.ldr"
    if not ldConfigFile.exists():
        print(f"LDRAW COLMAP: ERROR, No LDConfig.ldr file exists: '{ldConfigFile}'")
        return False
 
    with open(str(ldConfigFile), "r", encoding="utf-8-sig") as f:
        lines = f.readlines()
 
    if not lines:
        print(f"LDRAW COLMAP: ERROR, Corrupted LDConfig.ldr file contains no lines! '{ldConfigFile}'")
        return False
 
    outputData = {}
    for l in lines:
        l = l.strip()
        if not l or l[0] != "0":
            continue
        lArgs = l[1:].split()
 
        if len(lArgs) < 6 or lArgs[0] != "!COLOUR":
            continue
 
        # !COLOUR <name> CODE <n> VALUE <#hex> EDGE <#hex> [ALPHA n] [LUMINANCE n] [finish] [MATERIAL ...]
        colName = lArgs[1]
        colCode = lArgs[3]
        hexVal = lArgs[5]
 
        entry = {"name": colName, "col": hexVal}
 
        if "ALPHA" in lArgs:
            entry["alpha"] = int(lArgs[lArgs.index("ALPHA") + 1])

        if "LUMINANCE" in lArgs:
            entry["luminance"] = int(lArgs[lArgs.index("LUMINANCE") + 1])
 
        # Finish: a bare keyword (CHROME, PEARLESCENT, METAL, RUBBER), else the
        # MATERIAL type (GLITTER, SPECKLE, FABRIC), else plain SOLID.
        tail = lArgs[6:]
        finish = next((k for k in FINISH_KEYWORDS if k in tail), None)
        if finish is None and "MATERIAL" in tail:
            finish = tail[tail.index("MATERIAL") + 1]
        entry["finish"] = finish or "SOLID"
 
        outputData[colCode] = entry
 
    outFile = Path(__file__).parent / "colMap.json"
    with open(outFile, "w", encoding="utf-8") as f:
        json.dump(outputData, f, indent=1)

    colMap = outputData
    return True

def getColMap(rebuild=False):
    global colMap
    # Check memory first
    if colMap and (not rebuild):
        return colMap

    # Then cache
    partLib = library.getPartLibrary()
    if partLib:
        cacheFile = Path(__file__).parent  / "colMap.json"
        if cacheFile.exists() and (not rebuild):
            with open(str(cacheFile), "r", encoding="utf-8") as f:
                data = json.load(f)
            colMap = data
            return data
    
    # Then build
    success = buildColMap()
    if not success:
        print("LDRAW COLMAP: ERROR, Error building color map")
        return None
    return colMap

def clrColMap():
    global colMap
    colMap = None

def srgbToLinear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def rgbaFromCode(colCode):
    # Linear RGBA for an LDraw code, for obj.color (which is scene-linear)
    cMap = getColMap()
    if not cMap:
        return None
    entry = cMap.get(str(parseCol(colCode)))
    if entry is None:
        print(f"LDRAW COLMAP: WARNING, Color not found for code '{colCode}'")
        return None
    r, g, b = (srgbToLinear(c) for c in hexToRgb(entry["col"]))
    a = entry.get("alpha", 255) / 255.0
    return (r, g, b, a) 

def hexToRgb(hexVal):
    h = hexVal.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
