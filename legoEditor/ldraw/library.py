from pathlib import Path
from functools import lru_cache

partLib = None 
shadowLib = None 

def validatePath(path):
    cleanPath = Path(path).expanduser().resolve()
    if not cleanPath.is_dir():
        raise FileNotFoundError(f"Path '{cleanPath}' is not a directory.")
    else:
        return cleanPath
    
def setPartLibrary(newPath):
    global partLib
    partLib = validatePath(newPath)
    clearCaches()

def setShadowLibrary(newPath):
    global shadowLib
    shadowLib = validatePath(newPath)
    clearCaches()

def getPartLibrary():
    return partLib

def getShadowLibrary():
    return shadowLib

@lru_cache(maxsize=1)
def getSearchDirs():
    root = getPartLibrary()
    return (root / "p", root / "parts", root / "models")

@lru_cache(maxsize=None)
def resolvePart(relPath):
    name = relPath.replace("\\", "/").strip().lower()
    for base in getSearchDirs():
        candidate = base / name
        if candidate.exists():
            return candidate
    return None

@lru_cache(maxsize=None)
def resolveTexture(name, fileDir=None):
    name = name.strip('"').replace("\\", "/")

    bases = []
    if fileDir:
        bases.append(Path(fileDir))  # folder of the file being parsed
    if partLib:
        bases += [partLib / "parts", partLib / "p", partLib / "models"]

    # Spec: try "textures/<name>" across the whole search path,
    # then repeat without the prefix
    for rel in ("textures/" + name, name):
        for base in bases:
            p = base / rel
            if p.is_file():
                return p
    return None

@lru_cache(maxsize=1)
def getShadowSearchDirs():
    # The LDCad shadow library mirrors the official library layout under
    # "offLib" (offLib/parts, offLib/p). Also accept the shadow path pointing
    # directly at the offLib folder, and an unofficial "unOffLib" tree.
    root = getShadowLibrary()
    if root is None:
        return ()
    dirs = []
    for sub in ("offLib", "unOffLib", "."):
        base = (root / sub).resolve()
        for folder in ("parts", "p"):
            candidate = base / folder
            if candidate.is_dir() and candidate not in dirs:
                dirs.append(candidate)
    return tuple(dirs)
 
@lru_cache(maxsize=None)
def resolveShadowPart(file):
    # Map a resolved library file to its shadow counterpart, or None.
    # The shadow tree mirrors the official one (parts/, p/), possibly under offLib/.
    if partLib is None:
        return None
    shadowDirs = getShadowSearchDirs()
    if not shadowDirs:
        return None
    file = Path(file).resolve()
    for base in getSearchDirs():
        try:
            rel = file.relative_to(base)
        except ValueError:
            continue
        for sDir in shadowDirs:
            if sDir.name == base.name:
                candidate = sDir / rel
                if candidate.is_file():
                    return candidate
        return None
    return None
 
@lru_cache(maxsize=None)
def resolveShadowRef(relPath):
    # Resolve a SNAP_INCL ref (e.g. "s\\3003s01.dat", "connhole.dat") directly in
    # the shadow library. Official p/ is searched before parts/, as for parts.
    name = relPath.replace("\\", "/").strip().lower()
    dirs = sorted(getShadowSearchDirs(), key=lambda d: d.name != "p")
    for sDir in dirs:
        candidate = sDir / name
        if candidate.is_file():
            return candidate
    return None
 
def clearCaches():
    getSearchDirs.cache_clear()
    getShadowSearchDirs.cache_clear()
    resolvePart.cache_clear()
    resolveShadowPart.cache_clear()
    resolveShadowRef.cache_clear()
    resolveTexture.cache_clear()
    try:
        from . import snapping 
        snapping.clearCaches()
    except ImportError:
        pass
    try:
       from . import colors
       colors.clrColMap()
   except ImportError:
       pass
