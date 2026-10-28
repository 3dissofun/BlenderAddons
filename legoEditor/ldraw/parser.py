import numpy as np
import shlex
from pathlib import Path

from . import library, textures
from .snaps import snapping
from .col import colors

def buildFace(lArgs, nVerts, col, matrix, reverse, tex, output):
    # Build a triangle (nVerts=3) or quad (nVerts=4) from a type 3 or 4 line
    colCode = colors.parseCol(lArgs[0])
    if colCode == 16:
        colCode = col  # inherit from parent

    # Shape points as a 2D array of [x,y,z],[x2,y2,z2]...
    newPoints = np.array(lArgs[1:1 + nVerts * 3], dtype=np.float64).reshape(nVerts, 3)
    newPoints = newPoints @ matrix[:3, :3].T + matrix[:3, 3]  # Transform by parent matrix

    if reverse:
        newPoints = newPoints[::-1]

    output["faceQuads"].append(True if nVerts == 4 else False)
    output["points"].extend(newPoints.astype(np.float32).ravel())
    output["faceCols"].append(colCode)

    # UVs, computed on the final (possibly reversed) points so they stay paired
    uvPoints = None
    if tex:
        projType = tex["projType"]
        projParams = tex["projParams"]
        if projType == "PLANAR":
            uvPoints = textures.planarUvMap(newPoints, projParams)
        elif projType == "CYLINDRICAL":
            uvPoints = textures.cylinderUvMap(newPoints, projParams)
        elif projType == "SPHERICAL":
            uvPoints = textures.sphericalUvMap(newPoints, projParams)
        else:
            print(f"LDRAW PARSER: WARNING, Unrecognized projection type: '{projType}'")

    if uvPoints is None:
        # Untextured or failed: pad so faceUvs stays aligned with points
        output["faceUvs"].extend([0.0] * (2 * nVerts))
        output["faceTex"].append(-1)
    else:
        output["faceUvs"].extend(np.asarray(uvPoints, dtype=np.float32).ravel())
        output["faceTex"].append(tex["texIndex"])

def parsePart(file,col=16,matrix=np.eye(4),flip=False,output=None,parentTex=None):
    # Returns all mesh/texture files given a file path input
    # Expects file is formatted according to ldraw standards
    # Supports winding order/BFC
    # Supports TEXMAP commands

    # Determine if root
    if output is None:
        isTop=True
    else:
        isTop=False
    
    # Output Data
    if isTop:
        output = {}
        # All output data is structured in numpy arrays
        output["faceQuads"] = [] # single bit to flag if the next indicies are tris or quads, if 1 means next 4 points are one face, if 0 next 3 points
        output["points"] = [] # array of float32 point positions, x1,y1,z1,x2,y2,z2
        output["faceCols"] = [] # list of uint8 ldraw color codes for each face
        output["faceUvs"] = [] # list of float32 UV coordinates for each face
        output["faceTex"] = [] # index of texture map each face is using, -1 for none. Maps into "textures" list
        output["textures"] = [] # list of filepaths for textures
        output["connectors"] = [] # Array of dictionaries containg snap info
        output["_snapRoot"] = np.linalg.inv(matrix) # strips the root matrix back out of snaps
 
    # Connector support: snaps inherited from subfiles land after this index,
    # then this file's own shadow info is applied at the end
    snapStart = len(output["connectors"])

    # TEXMAP Data
    texStack = []
    nextTex = None # set by NEXT, cleared after the next geometry line
    inTexFallback = False # Track if we are in a texture fallback block

    # BFC Data
    isMirrored = np.linalg.det(matrix[:3, :3]) < 0
    cw = False # Clockwise winding order from file
    invertNext = False # Track if we are inverting the next section

    with open(file,"r",encoding="utf-8-sig") as f:
        lines = f.readlines()

    for l in lines:
        if not l.strip(): # Blank lines allowed
            continue
        l = l.lstrip().strip("\n") # Remove leading whitespace, allowed in specification
        lType = int(l[0]) # Integer defining the line type according to ldraw
        lArgs = l[1:].lstrip().split()

        if not lArgs:
            continue

        pendingNext = nextTex
        nextTex = None

        if inTexFallback and not (lType == 0 and lArgs[:2] == ["!TEXMAP", "END"]):
            continue # Skip TEXMAP fallback block unless at the end of it

        # "0 !:" -> unwrap and treat as a normal line
        if lType == 0 and lArgs[0] == "!:":
            if len(lArgs) < 2:
                continue
            l = " ".join(lArgs[1:])
            lType = int(l[0])
            lArgs = l[1:].split()

        activeTex = pendingNext or (texStack[-1] if texStack else None) or parentTex

        match lType:
            case 0:
                # determine if comment or meta command
                leadingArg = lArgs[0]
                if leadingArg.startswith("//"):
                    continue

                # BFC Language extension support
                elif leadingArg == "BFC":
                    flags = lArgs[1:]
                    if "INVERTNEXT" in flags:
                        invertNext = True

                    if "CW" in flags:
                        cw = True
                    elif "CCW" in flags:
                        cw = False
                
                # !TEXMAP Language extension support
                elif leadingArg == "!TEXMAP":
                    try:
                        # posix=False keeps backslashes in Windows paths intact.
                        tArgs = shlex.split(l[1:], posix=False)
                    except ValueError:
                        print(f"LDRAW PARSER: WARNING, Malformed TEXMAP line: '{l}'")
                        continue

                    if len(tArgs) < 2:
                        continue

                    if tArgs[1] == "START": 
                        texStack.append(textures.parseTexmap(tArgs[2:], matrix, output, str(Path(file).parent)))
                    elif tArgs[1] == "NEXT":
                        nextTex = textures.parseTexmap(tArgs[2:], matrix, output, str(Path(file).parent))
                    elif tArgs[1] == "FALLBACK":
                        inTexFallback = True
                    elif tArgs[1] == "END":
                        if texStack: # END without a START in this file has no effect
                            texStack.pop()
                        inTexFallback = False
                        
                elif leadingArg == "STEP":
                    texStack.clear()

                # !COLOUR Language extension support
                elif leadingArg == "!COLOUR":
                    print("COLOUR COMMAND")

            case 1:
                # include another file
                colCode = colors.parseCol(lArgs[0])
                if colCode == 16:
                    colCode = col  # inherit from parent
                x, y, z, a, b, c, d, e, f, g, h, i = (float(v) for v in lArgs[1:13])
                incMatrix = np.array([
                    [a, b, c, x],
                    [d, e, f, y],
                    [g, h, i, z],
                    [0, 0, 0, 1],])

                relPath = " ".join(lArgs[13:]) # allow spaces in relative path name
                absPath = library.resolvePart(relPath)
                if absPath is None:
                    print(f"LDRAW PARSER: WARNING, Part could not be found: '{relPath}'")
                else:
                    actuallyFlip = flip ^ invertNext
                    parsePart(str(absPath),colCode,matrix @ incMatrix,actuallyFlip,output,activeTex)
                invertNext = False

            case 3 | 4:
                nVerts = 3 if lType == 3 else 4
                # This function mutates the output dict
                buildFace(lArgs, nVerts, col, matrix, cw ^ flip ^ isMirrored, activeTex, output)

            case 2 | 5:
                # Ignored Command types
                pass

    snapping.applyShadow(file, matrix, output, snapStart)
 
    if isTop:
        output["connectors"] = snapping.dedupe(output["connectors"])
        del output["_snapRoot"]

    return output
