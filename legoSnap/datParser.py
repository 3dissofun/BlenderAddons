import numpy as np
import os

commandMap = {"0":"free","1":"include","2":"edge","3":"triangle","4":"quad","5":"optLine"}

def parseCommands(filePath):
    with open(filePath,"r",encoding='UTF-8') as f:
        rawLines = f.readlines()

    parsedResult = []
    for line in rawLines:
        if not line:
            continue
        commandType = line[0]
        command = commandMap.get(commandType,None)
        if not command:
            continue
        commandArgs = line[1:].rstrip().strip("\n")
        if not commandArgs:
            continue
        parsedResult.append((command,commandArgs))

    return parsedResult

def parseInclude(args):
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
    parts = args.split()
    colour = int(parts[0])
    coords = [float(v) for v in parts[1 : 1 + numVerts * 3]]
    points = [tuple(coords[k:k+3]) for k in range(0, len(coords), 3)]
    return colour, points

def transformPoints(matrix, points):
    pts = np.array([[*p, 1] for p in points])
    transformed = (matrix @ pts.T).T
    return [tuple(row[:3]) for row in transformed]

def resolveFile(filename,localDir):
    return None

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

output = flatten("3001.dat")
for o in output:
    print(o)
