from pathlib import Path
from functools import lru_cache

partLib = None
shadowLib = None
overlayDir = None  # folder of files unpacked from an MPD, searched first

# ---------------------------------------------------------------------------
# Library roots
# ---------------------------------------------------------------------------

def validatePath(path):
    cleanPath = Path(path).expanduser().resolve()
    if not cleanPath.is_dir():
        raise FileNotFoundError(f"Path '{cleanPath}' is not a directory.")
    return cleanPath

def setPartLibrary(newPath):
    global partLib
    partLib = validatePath(newPath)
    clearCaches()

def setShadowLibrary(newPath):
    global shadowLib
    shadowLib = validatePath(newPath)
    clearCaches()

def setOverlay(path):
    # Point at a folder of files unpacked from an MPD (or None to remove it).
    # Overlay files take precedence over the library, as the MPD spec requires.
    global overlayDir
    overlayDir = Path(path).resolve() if path else None
    clearCaches()

def getPartLibrary():
    if partLib is None:
        print("LDRAW LIBRARY: WARNING, No part library set")
    return partLib

def getShadowLibrary():
    if shadowLib is None:
        print("LDRAW LIBRARY: WARNING, No shadow library set")
    return shadowLib

def getOverlay():
    return overlayDir

def _normalise(relPath):
    return relPath.strip().strip('"').replace("\\", "/").lower()

# ---------------------------------------------------------------------------
# Parts
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def getSearchDirs():
    # Overlay first, then the official library. Empty if nothing is set.
    dirs = []
    if overlayDir:
        dirs.append(overlayDir)
    root = getPartLibrary()
    if root:
        dirs += [root / "p", root / "parts", root / "models"]
    return tuple(dirs)

@lru_cache(maxsize=None)
def resolvePart(relPath):
    name = _normalise(relPath)
    for base in getSearchDirs():
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None

# ---------------------------------------------------------------------------
# Textures
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def resolveTexture(name, fileDir=None):
    name = name.strip().strip('"').replace("\\", "/")

    bases = []
    if fileDir:
        bases.append(Path(fileDir))  # folder of the file being parsed
    if overlayDir and overlayDir not in bases:
        bases.append(overlayDir)     # !DATA blocks unpacked from an MPD
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

# ---------------------------------------------------------------------------
# Shadow library (LDCad snap info)
# ---------------------------------------------------------------------------

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
    # Map a resolved part file to its shadow counterpart, or None.
    # Library files mirror their own folder (parts/ -> shadow parts/, p/ -> shadow p/).
    # Overlay files have no such folder, so their name is tried under parts/ then p/.
    shadowDirs = getShadowSearchDirs()
    if not shadowDirs:
        return None
    file = Path(file).resolve()
    for base in getSearchDirs():
        try:
            rel = file.relative_to(base)
        except ValueError:
            continue
        folders = ("parts", "p") if base == overlayDir else (base.name,)
        for folder in folders:
            for sDir in shadowDirs:
                if sDir.name == folder:
                    candidate = sDir / rel
                    if candidate.is_file():
                        return candidate
        return None  # first matching base wins, as in resolvePart
    return None

@lru_cache(maxsize=None)
def resolveShadowRef(relPath):
    # Resolve a SNAP_INCL ref (e.g. "s\\3003s01.dat", "connhole.dat") directly in
    # the shadow library. Official p/ is searched before parts/, as for parts.
    name = _normalise(relPath)
    dirs = sorted(getShadowSearchDirs(), key=lambda d: d.name != "p")
    for sDir in dirs:
        candidate = sDir / name
        if candidate.is_file():
            return candidate
    return None

# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------

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
