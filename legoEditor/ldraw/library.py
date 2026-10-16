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
    # Map a resolved library file to its shadow counterpart, or None
    if shadowLib is None or partLib is None:
        return None
    file = Path(file).resolve()
    for base in getSearchDirs():
        try:
            rel = file.relative_to(base)
        except ValueError:
            continue
        candidate = shadowLib / base.name / rel
        return candidate if candidate.is_file() else None
    return None

def clearCaches():
    getSearchDirs.cache_clear()
    getShadowSearchDirs.cache_clear()
    resolvePart.cache_clear()
    resolveShadowPart.cache_clear()
    resolveTexture.cache_clear()
