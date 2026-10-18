from pathlib import Path

try:
    from . import library
except:
    import library

FINISH_KEYWORDS = ("CHROME", "PEARLESCENT", "METAL", "RUBBER")
 
def buildColMap():
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
        colCode = int(lArgs[3])
        hexVal = lArgs[5]
 
        entry = {"name": colName, "col": hexVal}
 
        if "ALPHA" in lArgs:
            entry["alpha"] = int(lArgs[lArgs.index("ALPHA") + 1])
 
        # Finish: a bare keyword (CHROME, PEARLESCENT, METAL, RUBBER), else the
        # MATERIAL type (GLITTER, SPECKLE, FABRIC), else plain SOLID.
        tail = lArgs[6:]
        finish = next((k for k in FINISH_KEYWORDS if k in tail), None)
        if finish is None and "MATERIAL" in tail:
            finish = tail[tail.index("MATERIAL") + 1]
        entry["finish"] = finish or "SOLID"
 
        outputData[colCode] = entry
 
    return outputData

library.setPartLibrary(r"C:\Users\Josh\Downloads\complete\ldraw")
data = buildColMap()

for k,v in data.items():
    print(f"{k}:{v}")
