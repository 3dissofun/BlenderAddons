import numpy as np

def normaliseName(name):
    # returns a lowercase string with slashes replaced as per ldraw standards
    return name.strip().lower().replace("\\", "/")

def parseMpd(file):
    # function to take an mpd file and return model->line block defining it,
    with open(file,"r",encoding="utf-8-sig") as f:
        lines = f.readlines()

    allModels = {}
    currentModel = "unnamed"

    for l in lines:
        if not l.strip(): # Blank lines allowed
            continue
        orgLine = l
        l = l.lstrip().strip("\n") # Remove leading whitespace, allowed in specification
        lType = int(l[0]) # Integer defining the line type according to ldraw
        lArgs = l[1:].lstrip().split()
        argC = len(lArgs)

        if not lArgs:
            continue

        match lType:
            case 0:
                if lArgs[0] == "FILE":
                    if argC < 2:
                        continue
                    modelName = normaliseName(" ".join(lArgs[1:]))
                    if modelName in allModels:
                        # spec defines first model name wins
                        continue
                    currentModel = modelName
                    allModels[currentModel] = [] # list of lines relating to the model

                elif currentModel != "unnamed":
                    allModels[currentModel].append(orgLine.split())
            
            case 1 | 3 | 4:
                allModels[currentModel].append(orgLine.split())

    return allModels

def buildModelTables(allModels):
    # Turns parseMpd output into indexed tables.
    # Returns:
    #   modelNames: list of submodel names (index = model id)
    #   partNames:  list of part names (index = part id, also the mesh cache order)
    #   tables:     list, one entry per model, of column arrays:
    #       kind   (bool)        0 for part or 1 for model
    #       index  (int32)       into partNames or modelNames, depending on kind
    #       colour (int32)       LDraw colour code, may be 16 (inherit)
    #       matrix (float64 Nx4x4) local transform relative to this model
    #       step   (int32)       local step number inside this model, starting at 0
 
    # Embedded .dat blocks are parts, not submodels
    modelNames = [n for n in allModels if not n.endswith(".dat")]
    modelLookup = {n: i for i, n in enumerate(modelNames)}
 
    partNames = []
    partLookup = {}
 
    tables = []
    for name in modelNames:
        kinds, indices, colours, matrices, steps = [], [], [], [], []
        step = 0
 
        for line in allModels[name]:
            if line[0] == "0":
                if len(line) > 1 and line[1] == "STEP":
                    step += 1
                continue
            if line[0] != "1" or len(line) < 15:
                continue
 
            ref = normaliseName(" ".join(line[14:]))
            if ref in modelLookup:
                kinds.append(True)
                indices.append(modelLookup[ref])
            else:
                if ref not in partLookup:
                    partLookup[ref] = len(partNames)
                    partNames.append(ref)
                kinds.append(False)
                indices.append(partLookup[ref])
 
            # NOTE this part can be improved later to support direct colors too
            if line[1].isdigit():
                colours.append(line[1])
            else:
                colours.append(0)
            x, y, z, a, b, c, d, e, f, g, h, i = (float(v) for v in line[2:14])
            matrices.append([[a, b, c, x],
                             [d, e, f, y],
                             [g, h, i, z],
                             [0, 0, 0, 1]])
            steps.append(step)
 
        tables.append({
            "kind": np.array(kinds, dtype=bool),
            "index": np.array(indices, dtype=np.int32),
            "colour": np.array(colours, dtype=np.int32),
            "matrix": np.array(matrices, dtype=np.float64).reshape(-1, 4, 4),
            "step": np.array(steps, dtype=np.int32),
        })
 
    return modelNames, partNames, tables

if __name__ == "__main__":
    allModels = parseMpd(r"C:\Users\User\Desktop\Projects\Lego_Project\Models\Modular buildings\10243 - Parisian Restaurant.mpd")
    modelNames, partNames, tables = buildModelTables(allModels)
    print(modelNames)
    print(partNames)
