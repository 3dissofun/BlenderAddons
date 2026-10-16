import numpy as np

from . import library

def loadTexture(name, fileDir=None):
    texPath = library.resolveTexture(name, fileDir)
    if not texPath:
        print(f"LDRAW PARSER: WARNING, Texture could not be resolved {name}")
        return None
    return texPath

def parseTexmap(tArgs, matrix, output, fileDir=None):
    # Parse a TEXMAP START/NEXT line from the method onwards, register its
    # texture(s) in output["textures"], and return a texture dict (or None).
    # fileDir: folder of the file containing the command, searched first for textures.
    TEXMAP_PARAM_COUNTS = {"PLANAR": 9, "CYLINDRICAL": 10, "SPHERICAL": 11}

    if not tArgs:
        print("LDRAW PARSER: WARNING, TEXMAP command missing projection type")
        return None

    projType = tArgs[0].upper()
    nParams = TEXMAP_PARAM_COUNTS.get(projType)
    if nParams is None:
        print(f"LDRAW PARSER: WARNING, Unrecognized uv map format '{tArgs[0]}'")
        return None

    if len(tArgs) < nParams + 2:  # method + params + filename
        print(f"LDRAW PARSER: WARNING, TEXMAP {projType} has too few arguments")
        return None

    try:
        projParams = np.array(tArgs[1:1 + nParams], dtype=np.float64)
    except ValueError:
        print(f"LDRAW PARSER: WARNING, TEXMAP {projType} has non-numeric parameters")
        return None

    # Texture names: the main texture, plus GLOSSMAP if present
    rest = tArgs[1 + nParams:]
    names = [rest[0]]
    if len(rest) >= 3 and rest[1].upper() == "GLOSSMAP":
        names.append(rest[2])

    # Resolve and register each texture, reusing existing entries
    indices = []
    for name in names:
        path = loadTexture(name, fileDir)
        if path is None:
            indices.append(-1)
            continue
        path = str(path)
        if path not in output["textures"]:
            output["textures"].append(path)
        indices.append(output["textures"].index(path))

    if indices[0] < 0:
        return None  # main texture missing: build the faces untextured

    # Transform projection points into the same space as the face points.
    # Trailing angles (cylindrical a, spherical a b) are left untouched.
    pts = projParams[:9].reshape(3, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    projParams = np.concatenate([pts.ravel(), projParams[9:]])

    return {
        "projType": projType,
        "projParams": projParams,
        "texIndex": indices[0],
        "glossIndex": indices[1] if len(indices) > 1 else -1,
    }

def planarUvMap(points, params):
    p1, p2, p3 = params[0:3], params[3:6], params[6:9]
    uAxis, vAxis = p2 - p1, p3 - p1
    d = points - p1
    u = d @ uAxis / (uAxis @ uAxis)
    v = d @ vAxis / (vAxis @ vAxis)
    return np.stack([u, v], axis=1).astype(np.float32)

def _fixSeam(u, angleRange):
    # Stop a face that crosses the +-180 degree line from smearing the whole
    # texture across it. angleRange is in radians.
    turn = 2 * np.pi / angleRange  # one full revolution, in u units
    if u.max() - u.min() > turn / 2:
        mid = (u.max() + u.min()) / 2
        u = np.where(u < mid, u + turn, u)
    return u

def cylinderUvMap(points, params):
    # P1 = bottom centre, P2 = top centre,
    # P3 = point on the bottom edge where the texture's bottom-centre touches,
    # a  = angular extent in degrees, mapped from -a/2 to +a/2 around P1->P3.
    p1, p2, p3 = params[0:3], params[3:6], params[6:9]
    a = np.radians(params[9])

    axis = p2 - p1
    height = np.linalg.norm(axis)
    if height == 0 or a == 0:
        return None
    w = axis / height                    # unit cylinder axis

    r0 = p3 - p1
    r0 = r0 - (r0 @ w) * w               # project onto the base plane
    r0Len = np.linalg.norm(r0)
    if r0Len == 0:
        return None
    r0 /= r0Len                          # unit vector to the texture centre (u = 0.5)
    s = np.cross(w, r0)                  # perpendicular, in the base plane

    d = points - p1
    v = (d @ w) / height                 # distance from base / |P1P2|
    angle = np.arctan2(d @ s, d @ r0)    # signed angle from P1->P3
    u = _fixSeam(angle / a + 0.5, a)

    return np.stack([u, v], axis=1).astype(np.float32)

def sphericalUvMap(points, params):
    # P1 = centre, P2 = point where the texture centre touches,
    # P3 = forms (with P1, P2) the plane that bisects the texture horizontally,
    # a, b = horizontal / vertical extents in degrees.
    p1, p2, p3 = params[0:3], params[3:6], params[6:9]
    a, b = np.radians(params[9]), np.radians(params[10])
    if a == 0 or b == 0:
        return None

    c = p2 - p1
    cLen = np.linalg.norm(c)
    if cLen == 0:
        return None
    c /= cLen                            # unit vector to the texture centre

    n1 = np.cross(c, p3 - p1)            # normal of plane P1 (horizontal bisector)
    n1Len = np.linalg.norm(n1)
    if n1Len == 0:
        return None                      # P1, P2, P3 are collinear
    n1 /= n1Len
    n2 = np.cross(n1, c)                 # lies in P1; normal of plane P2

    d = points - p1
    along = d @ c
    uAngle = np.arctan2(d @ n2, along)   # angle within plane P1
    vAngle = np.arctan2(d @ n1, along)   # angle within plane P2

    u = _fixSeam(uAngle / a + 0.5, a)
    v = vAngle / b + 0.5
    return np.stack([u, v], axis=1).astype(np.float32)
