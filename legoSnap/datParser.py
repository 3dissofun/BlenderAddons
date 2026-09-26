# The purpose of this file is to provide a function that takes in a .dat piece file, and returns:
# (listOfFaceData,listOfFaceColors,listOfConnectionAttributes)
# This should return enough data to properly construct basic geo for the piece, and set up snapping

import numpy as np
import os

commandMap = {"0":"free","1":"include","2":"edge","3":"triangle","4":"quad","5":"optLine"}
libraryDir = r"C:\Users\Josh\Downloads\complete\ldraw"
searchDirs = [os.path.join(libraryDir,"parts"), os.path.join(libraryDir,"p")]

def resolveFile(filename, localDir=""):
    clean = filename.replace("\\", os.sep)
    # check the folder the parent file lives in first
    if localDir:
        local = os.path.join(localDir, clean)
        if os.path.isfile(local):
            return local
    # then search the library
    for searchDir in searchDirs:
        candidate = os.path.join(searchDir, clean)
        if os.path.isfile(candidate):
            return candidate
    return None

def parseCommands(filePath):
    # This function reads raw .dat files and returns a list of
    # (commandType,commandArguments)
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

def transformPoints(matrix, points):
    # Transforms a list of points by a transform matrix
    # Returns a list of the transformed points
    pts = np.array([[*p, 1] for p in points])
    transformed = (matrix @ pts.T).T
    return [tuple(row[:3]) for row in transformed]

def flatten(filePath, matrix=np.eye(4), colour=16):
    parsed = parseCommands(filePath)
    localDir = os.path.dirname(filePath)
    faces = []

    for command, args in parsed:
        if command in ("triangle", "quad"):
            numVerts = 3 if command == "triangle" else 4
            faceColour, points = parsePolygon(args, numVerts)
            if faceColour == 16:
                faceColour = colour
            faces.append((faceColour, transformPoints(matrix, points)))

        elif command == "include":
            incColour, incMatrix, filename = parseInclude(args)
            if incColour == 16:
                incColour = colour
            childPath = resolveFile(filename, localDir)
            if childPath is None:
                print(f"WARNING: could not find {filename}")
                continue
            faces.extend(flatten(childPath, matrix @ incMatrix, incColour))

    return faces
