"""
The purpose of this file is to allow creating/extension of point cloud objects
It calls ldraws importer to get mesh data for the bricks, then creates objects in the correct order
for instancing on to the points by type in geo nodes
the order is in name order, so we prefix object names with a unique index.
Any points added to the point cloud then just get an index+1 value in the point attibute and matching index
Model = collection of points categorized together, default = misc
hopefully that makes some sense :)
"""
import bpy

import numpy as np

from ..ldraw.importer import importer

def makeInstancerGroup():
    # Makes from scratch a geometry nodes nodegroup that instances lego bricks onto points
    nodeGroup = bpy.data.node_groups.new('LegoInstancer_GN', 'GeometryNodeTree')
    nodeGroup.is_modifier = True
    nodes = nodeGroup.nodes
    links = nodeGroup.links
    interface = nodeGroup.interface

    # ---- interface: group inputs and outputs
    geometryIn = interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    geometryOut = interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')

    collectionIn = interface.new_socket('Collection', in_out='INPUT', socket_type='NodeSocketCollection')
    collectionIn.optional_label = True

    scaleFactorIn = interface.new_socket('ScaleFactor', in_out='INPUT', socket_type='NodeSocketFloat')
    scaleFactorIn.force_non_field = True
    scaleFactorIn.structure_type = 'SINGLE'
    scaleFactorIn.default_value = 1.0
    scaleFactorIn.min_value = 0.0

    # ---- nodes
    groupInput = nodes.new('NodeGroupInput')
    groupInput.location = (-592, -56)

    groupOutput = nodes.new('NodeGroupOutput')
    groupOutput.location = (476, 0)

    instanceOnPoints = nodes.new('GeometryNodeInstanceOnPoints')
    instanceOnPoints.location = (265, 4)
    instanceOnPoints.inputs['Pick Instance'].default_value = True

    collectionInfo = nodes.new('GeometryNodeCollectionInfo')
    collectionInfo.location = (15, -93)
    collectionInfo.inputs['Separate Children'].default_value = True

    typeAttribute = nodes.new('GeometryNodeInputNamedAttribute')
    typeAttribute.location = (16, -263)
    typeAttribute.data_type = 'INT'
    typeAttribute.inputs['Name'].default_value = 'type'

    rotationAttribute = nodes.new('GeometryNodeInputNamedAttribute')
    rotationAttribute.location = (16, -415)
    rotationAttribute.data_type = 'QUATERNION'
    rotationAttribute.inputs['Name'].default_value = 'rotation'

    setPosition = nodes.new('GeometryNodeSetPosition')
    setPosition.location = (16, 6)
    setPosition.inputs['Selection'].hide = True
    setPosition.inputs['Offset'].hide = True

    position = nodes.new('GeometryNodeInputPosition')
    position.location = (-344, -56)

    scalePosition = nodes.new('ShaderNodeVectorMath')
    scalePosition.location = (-173, -54)
    scalePosition.operation = 'SCALE'

    # ---- links
    links.new(groupInput.outputs['Geometry'], setPosition.inputs['Geometry'])
    links.new(groupInput.outputs['Collection'], collectionInfo.inputs['Collection'])
    links.new(groupInput.outputs['ScaleFactor'], scalePosition.inputs['Scale'])
    links.new(groupInput.outputs['ScaleFactor'], instanceOnPoints.inputs['Scale'])

    links.new(position.outputs['Position'], scalePosition.inputs[0])
    links.new(scalePosition.outputs['Vector'], setPosition.inputs['Position'])

    links.new(setPosition.outputs['Geometry'], instanceOnPoints.inputs['Points'])
    links.new(collectionInfo.outputs['Instances'], instanceOnPoints.inputs['Instance'])
    links.new(typeAttribute.outputs['Attribute'], instanceOnPoints.inputs['Instance Index'])
    links.new(rotationAttribute.outputs['Attribute'], instanceOnPoints.inputs['Rotation'])

    links.new(instanceOnPoints.outputs['Instances'], groupOutput.inputs['Geometry'])

    return nodeGroup

def getInstancingGroup():
    # function to return the LegoInstancer_GN nodegroup from the blend file if it exists
    # otherwise it makes the nodegroup from scratch
    existing = bpy.data.node_groups.get("LegoInstancer_GN",None)
    if existing:
        return exsiting
    existing = makeInstancerGroup()
    return existing

def setModifierInput(modifier, socketName, value):
    # small helper to set modifier input sockets by name
    identifier = next(item.identifier for item in modifier.node_group.interface.items_tree
        if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == socketName)
    getattr(modifier.properties.inputs, identifier).value = value

def importAsPoint(brickId, model="misc"):
    # method to create or extend the current models point cloud
    if not brickId.lower().endswith(".dat"):
        brickId = brickId + ".dat"

    brickMesh = importer.getBrickMesh(brickId)

    # get a collection for prototypes for this model, if it doesnt exist, make it
    protoCollName = model + "_Prototypes"
    existing = bpy.data.collections.get(protoCollName)
    if existing:
        protoColl = existing
    else:
        protoColl = bpy.data.collections.new(protoCollName)
        protoColl.hide_viewport = True
        protoColl.hide_render = True
        bpy.context.scene.collection.children.link(protoColl)

    # get the point cloud
    currentPc = bpy.data.objects.get(model,None)
    if (currentPc is None) or (currentPc.type != 'POINTCLOUD'):
        pc = bpy.data.pointclouds.new(model)
        pcObj = bpy.data.objects.new(model,pc)
        bpy.context.scene.collection.objects.link(pcObj)
        # set the instancing node group
        modifier = pcObj.modifiers.new(name="LegoInstancer", type='NODES')
        nodeGroup = getInstancingGroup()
        modifier.node_group = nodeGroup
        setModifierInput(modifier, "Collection", protoColl)
    else:
        pcObj = currentPc
        pc = pcObj.data

    # make the new point
    currentAmount = len(pc.points)
    pc.resize(currentAmount + 1)

    # Read type attribute
    typeAttr = None
    if currentAmount > 0:
        typeAttr = pc.attributes.get("type",None)
        if typeAttr is None:
            nextFreeType = 0
        else:
            typesAmount = len(typeAttr.data)
            if typesAmount > 0:
                typeVals = np.empty(typesAmount, dtype=np.int32)
                typeAttr.data.foreach_get("value",typeVals)
                nextFreeType = int(typeVals.max()) + 1
            else:
                nextFreeType = 0
    else:
        nextFreeType = 0

    # 3. check if the brickId already exists as a protoype, and if so, set that type
    prototype = next((o for o in protoColl.all_objects if o.data == brickMesh), None)
    if prototype is None:
        prototype = importer.getBrickObj(brickMesh)
        protoColl.objects.link(prototype)

    prototype.name = f"{nextFreeType:05d}"
    if typeAttr is None:
        typeAttr = pc.attributes.new("type","INT","POINT")
    typeAttr.data[currentAmount].value = nextFreeType
    
    # get the current max brick type index being used, and add 1 for the new brick index
    # this will define the prototype prefix on the brick object

def importAsModel(brickIdList,modeName):
    # method to import a list of bricks as a model
    pass
