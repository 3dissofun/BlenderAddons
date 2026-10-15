import numpy as np
from pathlib import Path

#from . import library, textures
import library, textures

def parsePart(file,col=16,matrix=np.eye(4),flip=False,output=None,activeTex={}):
    # Returns all mesh/texture files given a file path input
    # Expects file is formatted according to ldraw standards
    # Supports winding order/BFC
    # Supports TEXMAP commands

    # Output Data
    if output is None:
        output = {}
        # All output data is structured in numpy arrays
        output["faceQuads"] = [] # single bit to flag if the next indicies are tris or quads, if 1 means next 4 points are one face, if 0 next 3 points
        output["points"] = [] # array of float32 point positions, x1,y1,z1,x2,y2,z2
        output["faceCols"] = [] # list of uint8 ldraw color codes for each face
        output["faceUvs"] = [] # list of float32 UV coordinates for each face
        output["faceTex"] = [] # index of texture map each face is using, -1 for none. Maps into "textures" list
        output["textures"] = [] # list of filepaths for textures

    # Texture Data
    inTexFallback = False # Track if we are in a texture fallback block

    with open(file,"r",encoding="utf-8") as f:
        lines = f.readlines()


    isMirrored = np.linalg.det(matrix[:3, :3]) < 0
    cw = False # Clockwise winding order from file
    invertNext = False # Track if we are inverting the next section

    for l in lines:
        if not l.strip(): # Blank lines allowed
            continue
        l = l.lstrip().strip("\n") # Remove leading whitespace, allowed in specification
        lType = int(l[0]) # Integer defining the line type according to ldraw
        lArgs = l[1:].lstrip().split()

        if not lArgs:
            continue

        if inTexFallback and not (lType == 0 and lArgs[:2] == ["!TEXMAP", "END"]):
            continue # Skip TEXMAP fallback block unless at the end of it

        # "0 !:" -> unwrap and treat as a normal line
        if lType == 0 and lArgs[0] == "!:":
            l = " ".join(lArgs[1:])
            lType = int(l[0])
            lArgs = l[1:].split()
            print(l)

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
                    if len(lArgs) < 2:
                        continue
                    if lArgs[1] == "START": 
                        print(l)
                        uvMapType = lArgs[2]
                        if uvMapType == "PLANAR":
                            uvMapParams = np.array(lArgs[3:12], dtype=np.float64)
                            pngName = lArgs[12]
                        elif uvMapType == "CYLINDRICAL":
                            uvMapParams = np.array(lArgs[3:13], dtype=np.float64)
                            pngName = lArgs[13]
                        elif uvMapType == "SPHERICAL":
                            uvMapParams = np.array(lArgs[3:14], dtype=np.float64)
                            pngName = lArgs[14]
                        else:
                            print(f"LDRAW PARSER: WARNING, Unrecogonized uv map format '{uvMapType}'")
                            continue

                        resolvedTex = textures.loadTexture(pngName)
                        if not resolvedTex:
                            print(f"LDRAW PARSER: WARNING, Could not find texture '{pngName}'")
                            continue
                        resolvedTex = str(resolvedTex)
                        if resolvedTex not in output["textures"]:
                            # add it!
                            output["textures"].append(resolvedTex)
                            texIndex = len(output["textures"])

                        activeTex["projectionParams"] = uvMapParams
                        activeTex["texIndex"] = texIndex

                    elif lArgs[1] == "NEXT":
                        pass
                    elif lArgs[1] == "FALLBACK":
                        inTexFallback = True
                    elif lArgs[1] == "END":
                        inTexFallback = False

                # !COLOUR Language extension support
                elif leadingArg == "!COLOUR":
                    print("COLOUR COMMAND")

            case 1:
                # include another file
                colCode = int(lArgs[0])
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
                    parsePart(str(absPath),colCode,matrix @ incMatrix,actuallyFlip,output)
                invertNext = False

            case 3:
                # build a triangle
                colCode = int(lArgs[0])
                if colCode == 16:
                    colCode = col  # inherit from parent
                # Shape points as a 2d Array of [x,y,z],[x2,y2,z2]
                newPoints = np.array(lArgs[1:10], dtype=np.float64).reshape(3, 3)
                newPoints = newPoints @ matrix[:3, :3].T + matrix[:3, 3] # Transform by parent matrix

                if cw ^ flip ^ isMirrored:
                    newPoints = newPoints[::-1]

                output["faceQuads"].append(0)
                output["points"].extend(newPoints.astype(np.float32).ravel())
                output["faceCols"].append(colCode)

            case 4:
                # build a quad
                colCode = int(lArgs[0])
                if colCode == 16:
                    colCode = col  # inherit from parent
                # Shape points as a 2d Array of [x,y,z],[x2,y2,z2]
                newPoints = np.array(lArgs[1:13], dtype=np.float64).reshape(4, 3)
                newPoints = newPoints @ matrix[:3, :3].T + matrix[:3, 3] # Transform by parent matrix

                if cw ^ flip ^ isMirrored:
                    newPoints = newPoints[::-1]

                output["faceQuads"].append(1)
                output["points"].extend(newPoints.astype(np.float32).ravel())
                output["faceCols"].append(colCode)

            case 2 | 5:
                # Ignored Command types
                continue

    return output

library.setPartLibrary(r"C:\Users\Josh\Downloads\complete\ldraw")
#parsePart(r"C:\Users\Josh\Downloads\complete\ldraw\parts\3001.dat")
parsePart(r"C:\Users\Josh\Downloads\complete\ldraw\parts\191764.dat")
