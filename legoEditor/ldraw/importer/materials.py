"""
The intent of this file is to provide materials to the brick maker
It returns the global shared material which is constant and does not change
and also variable unique texture materials
All materials share the same node group sourced from an external file
Future Improvements:
    File needs a full inspection/clean up as is all claude
    external file preference rather than hardcoded
    if no external file, auto building a basic setupl
"""
import bpy
from pathlib import Path

SHADER_FILE = Path(__file__).parent.parent.parent / "external" / "shaders.blend"
SHADER_GROUP = "LEGO_SN"

def getShaderGroup():
    # Append the LEGO_SN node group from shaders.blend once, then reuse it
    group = bpy.data.node_groups.get(SHADER_GROUP)
    if group:
        return group
 
    if not SHADER_FILE.is_file():
        print(f"LDRAW IMPORTER: ERROR, shader file not found: '{SHADER_FILE}'")
        return None
 
    with bpy.data.libraries.load(str(SHADER_FILE), link=False) as (src, dst):
        if SHADER_GROUP in src.node_groups:
            dst.node_groups = [SHADER_GROUP]
        else:
            print(f"LDRAW IMPORTER: ERROR, node group '{SHADER_GROUP}' not found in '{SHADER_FILE}'")
 
    return bpy.data.node_groups.get(SHADER_GROUP)
 
def _newMaterial(name):
    # Fresh node material; returns (material, nodes, links, group node, col attribute node)
    # groupNode is None if the node group couldn't be loaded, in which case the
    # material falls back to a plain principled BSDF driven by the colour attribute.
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
 
    attr = nodes.new("ShaderNodeAttribute")
    attr.attribute_type = 'GEOMETRY'
    attr.attribute_name = "col"
 
    group = getShaderGroup()
    if group is None:
        attr.location = (bsdf.location.x - 500, bsdf.location.y)
        links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
        return mat, nodes, links, None, attr
 
    groupNode = nodes.new("ShaderNodeGroup")
    groupNode.node_tree = group
    groupNode.location = bsdf.location
    attr.location = (groupNode.location.x - 500, groupNode.location.y)
 
    # "col" is an int attribute, which the Attribute node exposes as Fac (float)
    codeSocket = groupNode.inputs["Col Code"]
    links.new(attr.outputs["Fac" if codeSocket.type == 'VALUE' else "Color"], codeSocket)
 
    # Shader output goes straight to the surface; anything else feeds Base Color
    out = groupNode.outputs[0]
    if out.type == 'SHADER':
        matOut = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL')
        links.new(out, matOut.inputs["Surface"])
        nodes.remove(bsdf)
    else:
        links.new(out, bsdf.inputs["Base Color"])
 
    return mat, nodes, links, groupNode, attr
 
def getSharedMaterial():
    # One material for every untextured face of every brick
    mat = bpy.data.materials.get("LEGO_MAT")
    if mat is None:
        mat = _newMaterial("LEGO_MAT")[0]
    return mat
 
def makeTexturedMaterial(name, texPaths):
    # One material for a whole textured brick. The "tex" face attribute selects
    # which texture (if any) each face shows. The textures are merged into one
    # sticker colour (black where no texture applies) and fed to the node group's
    # "Sticker Col" input. If the group also has a "Sticker Alpha" input, it gets
    # the merged texture alpha, so the group can tell sticker pixels from bare ones.
    mat, nodes, links, groupNode, colAttr = _newMaterial(name)
    if groupNode is None:
        return mat  # no node group, so there is no sticker input to drive
 
    x0, y0 = groupNode.location.x - 300 * (len(texPaths) + 1), groupNode.location.y
    colAttr.location = (x0, y0)
 
    texAttr = nodes.new("ShaderNodeAttribute")
    texAttr.attribute_type = 'GEOMETRY'
    texAttr.attribute_name = "tex"
    texAttr.location = (x0, y0 - 250)
 
    colour = None  # running sticker colour
    alpha = None   # running sticker alpha
    for i, texPath in enumerate(texPaths):
        x = x0 + 300 * (i + 1)
 
        img = nodes.new("ShaderNodeTexImage")
        img.image = bpy.data.images.load(texPath, check_existing=True)
        img.extension = 'CLIP'  # outside the projection, alpha = 0
        img.location = (x, y0 - 300)
 
        # 1 on faces whose tex index == i, else 0
        match = nodes.new("ShaderNodeMath")
        match.operation = 'COMPARE'
        match.inputs[1].default_value = i
        match.inputs[2].default_value = 0.5  # epsilon
        match.location = (x, y0 - 600)
        links.new(texAttr.outputs["Fac"], match.inputs[0])
 
        # image alpha on matching faces only
        fac = nodes.new("ShaderNodeMath")
        fac.operation = 'MULTIPLY'
        fac.location = (x, y0 - 100)
        links.new(img.outputs["Alpha"], fac.inputs[0])
        links.new(match.outputs[0], fac.inputs[1])
 
        # Layer this texture over the previous ones (black underneath the first)
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = 'RGBA'
        mix.location = (x, y0 + 150)
        links.new(fac.outputs[0], mix.inputs["Factor"])
        if colour is None:
            mix.inputs[6].default_value = (0.0, 0.0, 0.0, 1.0)
        else:
            links.new(colour, mix.inputs[6])           # A (previous stickers)
        links.new(img.outputs["Color"], mix.inputs[7]) # B (this texture)
        colour = mix.outputs[2]
 
        if alpha is None:
            alpha = fac.outputs[0]
        else:
            best = nodes.new("ShaderNodeMath")
            best.operation = 'MAXIMUM'
            best.location = (x, y0 - 100)
            links.new(alpha, best.inputs[0])
            links.new(fac.outputs[0], best.inputs[1])
            alpha = best.outputs[0]
 
    links.new(colour, groupNode.inputs["Sticker Col"])
    links.new(alpha, groupNode.inputs["Sticker Alpha"])
    useSticker = groupNode.inputs.get("Use Sticker")
    if useSticker is not None:
        useSticker.default_value = True

    return mat

