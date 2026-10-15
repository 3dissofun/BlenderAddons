import numpy as np

import library

def loadTexture(name):
    texPath = library.resolveTexture(name)
    if not texPath:
        print(f"LDRAW PARSER: WARNING, Texture could not be resolved {name}")
        return None
    return texPath

def planarUvMap(points, params):
    p1, p2, p3 = params[0:3], params[3:6], params[6:9]
    uAxis, vAxis = p2 - p1, p3 - p1
    d = points - p1
    u = d @ uAxis / (uAxis @ uAxis)
    v = d @ vAxis / (vAxis @ vAxis)
    return np.stack([u, v], axis=1).astype(np.float32)

def cylinderUvMap(params):
    pass

def sphericalUvMap(params):
    pass
