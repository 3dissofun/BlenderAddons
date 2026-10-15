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
    getSearchDirs.cache_clear()
    resolvePart.cache_clear()

def setShadowLibrary(newPath):
    global shadowLib
    shadowLib = validatePath(newPath)

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

def resolveTexture(name):
    name = name.strip('"').replace("\\", "/")
    for sub in ("parts/textures", "p/textures"):
        p = partLib / sub / name
        if p.is_file():
            return p
    return None

def clearCaches():
    getSearchDirs.cache_clear()
    resolvePart.cache_clear()
