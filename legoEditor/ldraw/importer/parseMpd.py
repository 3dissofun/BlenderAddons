import numpy as np
from pathlib import Path

def normaliseName(name):
    # returns a lowercase string with slashes replaced as per ldraw standards
    return name.strip().lower().replace("\\", "/")

def parseMpd(file):
    # Returns {model name: [line tokens]}, main model first.
    # Lines before any "0 FILE" (a plain .ldr, or an unwrapped main model)
    # belong to a model named after the file.
    with open(file, "r", encoding="utf-8-sig") as f:
        lines = f.readlines()

    default = normaliseName(Path(file).name)
    allModels = {default: []}
    currentModel = default

    for l in lines:
        tokens = l.split()
        if not tokens:
            continue

        if tokens[0] == "0" and len(tokens) > 1:
            if tokens[1] == "FILE":
                if len(tokens) < 3:
                    continue
                if currentModel == default and not allModels[default]:
                    del allModels[default]          # nothing came before the first FILE
                modelName = normaliseName(" ".join(tokens[2:]))
                if modelName in allModels:
                    currentModel = None             # spec: first model with a name wins, skip this block
                else:
                    currentModel = modelName
                    allModels[currentModel] = []
                continue
            if tokens[1] == "NOFILE":
                currentModel = None                 # e.g. !DATA blocks follow, not model lines
                continue

        if currentModel is not None and tokens[0] in "012345":
            allModels[currentModel].append(tokens)  # keep 2/5 so embedded parts keep edges

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
