# The purpose of this file is to provide a function that takes in a .dat piece file, and returns:
# (listOfFaceData,listOfFaceColors,listOfConnectionAttributes)
# This should return enough data to properly construct basic geo for the piece, and set up snapping

import numpy as np
import os

commandMap = {"0":"free","1":"include","2":"edge","3":"triangle","4":"quad","5":"optLine"}
libraryDir = r""
shadowDir = r""

# FILE PATH HELPERS

def resolveFile(filename, localDir=""):
    clean = filename.replace("\\", os.sep)
    # check the folder the parent file lives in first
    if localDir:
        local = os.path.join(localDir, clean)
        if os.path.isfile(local):
            return local
    # then search the library
    searchDirs = [os.path.join(libraryDir,"parts"), os.path.join(libraryDir,"p")]
    for searchDir in searchDirs:
        candidate = os.path.join(searchDir, clean)
        if os.path.isfile(candidate):
            return candidate
    print(f"DAT PARSER: WARNING, No File could be resolved in library for piece {clean}")
    return None

def getShadowFor(resolvedFilepath):
    # take in the final filepath of a piece .dat file and search for a matching piece in the shadow library
    if not shadowDir:
        return None
    relPath = os.path.relpath(resolvedFilepath,libraryDir)
    candidate = os.path.join(shadowDir, relPath)
    if os.path.isfile(candidate):
        return candidate
    else:
        return None

# COMMAND PARSERS

def parseCommands(filePath):
    # This function reads raw .dat files and returns a list of
    # (commandType,commandArguments)

    if not filePath:
        print("DAT PARSER: WARNING, No valid filepath given")
        return []

    with open(filePath,"r",encoding='UTF-8') as f:
        rawLines = f.readlines()

    parsedResult = []
    for line in rawLines:
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        commandType = parts[0]
        command = commandMap.get(commandType,None)
        if not command:
            continue
        commandArgs = line[1:].rstrip().strip("\n")
        if not commandArgs:
            continue
        parsedResult.append((command,commandArgs))

    return parsedResult

def parseInclude(args):
    # Function to take in an include command and parse out:
    # colorInt, positionVector, matrix, filenameToInclude
    # returns colorInt, transformMatrix, filename
    parts = args.split()
    colour = int(parts[0])
    x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
    a, b, c = float(parts[4]), float(parts[5]), float(parts[6])
    d, e, f = float(parts[7]), float(parts[8]), float(parts[9])
    g, h, i = float(parts[10]), float(parts[11]), float(parts[12])
    filename = " ".join(parts[13:])

    matrix = np.array([
        [a, b, c, x],
        [d, e, f, y],
        [g, h, i, z],
        [0, 0, 0, 1],
    ])
    return colour, matrix, filename

def parsePolygon(args, numVerts):
    # Takes a polygon command and returns a list of point colors and point positions
    parts = args.split()
    colour = int(parts[0])
    coords = [float(v) for v in parts[1 : 1 + numVerts * 3]]
    points = [tuple(coords[k:k+3]) for k in range(0, len(coords), 3)]
    return colour, points

def parseSnapLine(line):
    kind = line.split()[2]
    attrs = {}
    for chunk in line.split("[")[1:]: # 'gender=M] ', 'secs=R 6 4] ', ...
        body = chunk.split("]")[0] # 'secs=R 6 4'
        key, _, value = body.partition("=")
        attrs[key] = value
    return kind, attrs

def parseGrid(gridStr):
    # returns a list of (dx, 0, dz) offsets in the snap's local frame
    tokens = gridStr.split()

    def takeAxis(tokens):
        centred = tokens[0].upper() == "C"
        if centred:
            tokens = tokens[1:]
        return centred, int(tokens[0]), tokens[1:]

    centredX, countX, tokens = takeAxis(tokens)
    centredZ, countZ, tokens = takeAxis(tokens)
    stepX, stepZ = float(tokens[0]), float(tokens[1])

    def axisOffsets(count, step, centred):
        start = -(count - 1) * step / 2 if centred else 0.0
        return [start + k * step for k in range(count)]

    return [(dx, 0.0, dz)
            for dx in axisOffsets(countX, stepX, centredX)
            for dz in axisOffsets(countZ, stepZ, centredZ)]

def getSnaps(filePath,matrix):
    shadowPath = getShadowFor(filePath)
    if not shadowPath:
        return []
    snaps = []
    with open(shadowPath, encoding='UTF-8') as f:
        for line in f:
            if not line.startswith("0 !LDCAD SNAP_"):
                continue

            kind, attrs = parseSnapLine(line)
            pos = np.array([float(v) for v in attrs.get("pos", "0 0 0").split()])
            ori = np.array([float(v) for v in attrs.get("ori", "1 0 0 0 1 0 0 0 1").split()]).reshape(3, 3)

            gridStr = attrs.pop("grid", "")
            offsets = parseGrid(gridStr) if gridStr else [(0.0, 0.0, 0.0)]

            for off in offsets:
                local = pos + ori @ np.array(off)
                inst = dict(attrs) # separate copy per instance
                inst["worldPos"] = tuple((matrix @ np.append(local, 1.0))[:3])
                inst["worldOri"] = matrix[:3, :3] @ ori # every cell shares the orientation
                snaps.append((kind, inst))

    return snaps

def transformPoints(matrix, points):
    # Transforms a list of points by a transform matrix
    # Returns a list of the transformed points
    pts = np.array([[*p, 1] for p in points])
    transformed = (matrix @ pts.T).T
    return [tuple(row[:3]) for row in transformed]

# ENTRY POINT RECURSIVE FUNCTION

def flatten(filePath, matrix=np.eye(4), colour=16, windingOrder=False):
    # Filepath is the entry point and also current file that is being parsed from any nestued commands
    # matrix is the current transform of any nested pieces
    # Colour is the current ldraw color id, with 16 meaning inherit from parent
    # windingOrder is the current order in which faces should be constructed based on file commands  to ensure normals face correctly
    parsed = parseCommands(filePath)
    if not parsed:
        print(f"DAT PARSER: WARNING, Parsing failed for '{filePath}'")
        return [], []
    
    snaps = getSnaps(filePath,matrix)

    localDir = os.path.dirname(filePath)
    faces = []
    isMirrored = np.linalg.det(matrix[:3, :3]) < 0

    for i, (command, args) in enumerate(parsed):
        if command == "free":
            parts = args.split()
            if parts[:1] == ["BFC"]:
                # "CERTIFY CW" or "CW" switches to clockwise; a bare "CCW" switches back
                if "CW" in parts or ("CCW" in parts and "CERTIFY" not in parts):
                    windingOrder = not windingOrder

        elif command in ("triangle", "quad"):
            numVerts = 3 if command == "triangle" else 4
            faceColour, points = parsePolygon(args, numVerts)
            if faceColour == 16:
                faceColour = colour
            pts = transformPoints(matrix,points)
            # flip order of points if winding order or mirrored:
            if windingOrder ^ isMirrored:
                pts = pts[::-1]
            faces.append((faceColour, pts))

        elif command == "include":
            incColour, incMatrix, filename = parseInclude(args)
            if incColour == 16:
                incColour = colour
            childPath = resolveFile(filename, localDir)
            if childPath is None:
                print(f"WARNING: could not find {filename}")
                continue
            prevCommand, prevArgs = parsed[i - 1] if i > 0 else (None, "")
            invertNext = prevCommand == "free" and "INVERTNEXT" in prevArgs.split()
            childFaces,childSnaps = flatten(childPath, matrix @ incMatrix, incColour, windingOrder ^ invertNext)
            faces.extend(childFaces)
            snaps.extend(childSnaps)

    return faces, snaps
