import bpy
import os
import struct
import numpy as np
import subprocess
import zlib
import lzma
from collections import defaultdict

# -----------------GENERAL-------------------------------------

# Stuctures \/
structKeyblock = np.dtype([
    ('frame','<f4'),
    ('value','<f4'),
    ('hleft_x','<f4'),
    ('hleft_y','<f4'),
    ('hright_x','<f4'),
    ('hright_y','<f4'),
    ('interpolation','i1'),
    ('hltype','i1'),
    ('hrtype','i1'),
    ])

objTypeEnum = {"MESH":0,"ARMATURE":1,"CAMERA":3,"BONE":4,"OBJDATA":5}
objTypeEnumLookup = {v: k for k,v in objTypeEnum.items()}

compressTypeEnum = {"None":0,"zlib":1,"lzma":2}
compressTypeEnumLookup = {v: k for k,v in compressTypeEnum.items()}

# --------------------EXPORT-----------------------------------

# Helpers \/
def iterFcurves(action,slot):
    for layer in action.layers:
        for strip in layer.strips:
            cb = strip.channelbag(slot)
            if cb:
                yield from cb.fcurves

def iterAllFcurves(obj):
    # Yield fcurves from both the object and its data block
    if obj.animation_data and obj.animation_data.action:
        anim = obj.animation_data
        yield from iterFcurves(anim.action, anim.action_slot)
    
    if obj.data and obj.data.animation_data and obj.data.animation_data.action:
        dataAnim = obj.data.animation_data
        yield from iterFcurves(dataAnim.action, dataAnim.action_slot)

def getBoneName(fc):
    path = fc.data_path
    if path.startswith('pose.bones["'):
        return path[12 : path.index('"]', 12)]
    return None

def getKeypoints(fcurve):
    '''Return the keyblock datatype for all keys on a given fcurve'''
    keyPoints = fcurve.keyframe_points
    n = len(keyPoints)
    if n == 0:
        return 0, b''
    
    keys = np.empty(n, dtype=structKeyblock)
    fg = keyPoints.foreach_get

    co = np.empty(n*2, dtype=np.float32)
    hleft = np.empty(n*2, dtype=np.float32)
    hright = np.empty(n*2, dtype=np.float32)
    interp = np.empty(n, dtype=np.int8)
    hltype = np.empty(n, dtype=np.int8)
    hrtype = np.empty(n, dtype=np.int8)
    
    fg("co", co)
    keys['frame'] = co[0::2]
    keys['value'] = co[1::2]

    # handles can use same temp buffer
    fg("handle_left", co)
    keys['hleft_x'] = co[0::2]
    keys['hleft_y'] = co[1::2]

    fg("handle_right", co)
    keys['hright_x'] = co[0::2]
    keys['hright_y'] = co[1::2]

    fg("interpolation", keys['interpolation'])
    fg("handle_left_type", keys['hltype'])
    fg("handle_right_type", keys['hrtype'])

    return n, keys.tobytes()

def getHeader(context,options,numCurves,numObjs):
    magicBytes = 0x4441 # ACII = AD
    version = 1
    scene = context.scene
    fps = scene.render.fps/scene.render.fps_base
    compressOpt = options["compression"]
    compressEnum = compressTypeEnum.get(compressOpt)
    return struct.pack('<HHBfffii',magicBytes,version,compressEnum,fps,scene.frame_start,scene.frame_end,numCurves,numObjs)

def getAnimatedProps(objList):
    fcurves = []
    objects = []

    for obj in objList:
        hasObjAnim  = obj.animation_data and obj.animation_data.action
        hasDataAnim = obj.data and obj.data.animation_data and obj.data.animation_data.action

        if not (hasObjAnim or hasDataAnim):
            continue        

        if obj.type == "ARMATURE":
            objFcs = list(iterAllFcurves(obj))
            boneFcs = {}
            for fc in objFcs:
                bone = getBoneName(fc)
                if bone:
                    boneFcs.setdefault(bone,[]).append(fc)

            for bName, fcs in boneFcs.items():
                start = len(fcurves) # Index before extension
                fcurves.extend(fcs)
                objects.append((f"{obj.name}:{bName}","BONE",list(range(start,start + len(fcs)))))
        
        else:
            # Object-level curves
            if hasObjAnim:
                anim = obj.animation_data
                objFcs = list(iterFcurves(anim.action, anim.action_slot))
                if objFcs:
                    start = len(fcurves)
                    fcurves.extend(objFcs)
                    objects.append((obj.name, obj.type, list(range(start, len(fcurves)))))

            # Data-block curves (e.g. camera lens, light energy)
            if hasDataAnim:
                dataAnim = obj.data.animation_data
                dataFcs = list(iterFcurves(dataAnim.action, dataAnim.action_slot))
                if dataFcs:
                    start = len(fcurves)
                    fcurves.extend(dataFcs)
                    objects.append((obj.name, "OBJDATA", list(range(start, len(fcurves)))))

    return fcurves,objects

# Callable \/
def exportAdb(context,objList,filepath,options):
    print("Exporting adb file...")
    
    fcurves,objects = getAnimatedProps(objList)

    fcurveAmount = len(fcurves)
    objAmount = len(objects)
    compressOpt = options["compression"]

    with open(filepath,'wb') as f:
        buf = bytearray()
        # Header
        header = getHeader(context,options,fcurveAmount,objAmount)

        # Curve Blocks
        for fc in fcurves:
            keyCount, keyData = getKeypoints(fc)
            channelBytes = fc.data_path.encode('utf-8') # We use path bytes to tell the importer how long the pathName is, to efficiently pack and remove limits on path length
            buf += struct.pack('<iHH',keyCount, fc.array_index, len(channelBytes))
            buf += channelBytes
            buf += keyData
        
        # Object Blocks
        for name,objType,curveIds in objects:
            nameBytes = name.encode('utf-8')
            typeEnum = objTypeEnum.get(objType,255)
            startCurvesId = curveIds[0] if curveIds else 0
            buf += struct.pack('<BHHH',typeEnum,startCurvesId,len(curveIds),len(nameBytes)) # Curve Ids stored as startIndex + count
            buf += nameBytes

        f.write(header)
        if compressOpt == "zlib":
            f.write(zlib.compress(buf,level=9)) # Levels 1-9 for speed/size 
        elif compressOpt == "lzma":
            f.write(lzma.compress(buf))
        else:
            f.write(buf)

    return 0

# --------------------IMPORT-----------------------------------

# Helpers \/
def applyFcurves(obj,curveDataList, action, slot):
    # curveDataList is a list of dicts [{"channel":location%0,keys:sturctuednpArray}]

    for curveData in curveDataList:
        dataPath = curveData["channel"]
        arrayIndex = curveData["channelIndex"]
        keys = curveData["keys"]
        n = len(keys)

        # Find channelbag for this slot
        layer = action.layers[0]
        strip = layer.strips[0]
        cb = strip.channelbag(slot)

        fc = cb.fcurves.new(dataPath,index=arrayIndex)
        fc.keyframe_points.add(n)
        kps = fc.keyframe_points

        co = np.empty(n * 2, dtype=np.float32)
        co[0::2] = keys['frame']
        co[1::2] = keys['value']
        kps.foreach_set('co', co)

        hl = np.empty(n * 2, dtype=np.float32)
        hl[0::2] = keys['hleft_x']
        hl[1::2] = keys['hleft_y']
        kps.foreach_set('handle_left', hl)

        hr = np.empty(n * 2, dtype=np.float32)
        hr[0::2] = keys['hright_x']
        hr[1::2] = keys['hright_y']
        kps.foreach_set('handle_right', hr)

        # Use int32 for enum fields
        kps.foreach_set('interpolation',   keys['interpolation'].astype(np.int32))
        kps.foreach_set('handle_left_type', keys['hltype'].astype(np.int32))
        kps.foreach_set('handle_right_type', keys['hrtype'].astype(np.int32))

        fc.update()

def getOrCreateAction(target, actionCache, cacheKey):
    if cacheKey in actionCache:
        return actionCache[cacheKey]

    if not target.animation_data:
        target.animation_data_create()
    anim = target.animation_data

    action = bpy.data.actions.new(name=f"{cacheKey}_ADB_ImportedAction")
    anim.action = action
    slot = action.slots.new(id_type=target.id_type, name=cacheKey)
    anim.action_slot = slot

    layer = action.layers.new(name="Layer")
    strip = layer.strips.new(type='KEYFRAME')
    cb = strip.channelbags.new(slot)

    actionCache[cacheKey] = (action, slot)
    return action, slot

def remapBoneChannels(curveDataList, boneName):
    # Prefix bare data paths with pose.bones[...] for Maya-sourced bones
    remapped = []
    for cd in curveDataList:
        dataPath = cd["channel"]
        if not dataPath.startswith('pose.bones'):
            dataPath = f'pose.bones["{boneName}"].{dataPath}'
        remapped.append({"channel": dataPath, "channelIndex": cd["channelIndex"], "keys": cd["keys"]})
    return remapped

# Callable\/
def importAdb(context,objList,filepath):
    with open(filepath,'rb') as f:
        raw = f.read()
        header = raw[:25]
        body = raw[25:]

        magic,version,compressionEnum,fps,frameStart,frameEnd,numCurves,numObjs = struct.unpack_from('<HHBfffii',header,0)
        compressionType = compressTypeEnumLookup.get(compressionEnum)
        if compressionType == "zlib":
            buf = zlib.decompress(body)
        elif compressionType == "lzma":
            buf = lzma.decompress(body)
        elif compressionType == "None":
            buf = body
        else:
            print("Compression type unrecognized! Aborting")
            return 1

    # Setup object and bone mappings
    objByName = defaultdict(list)
    boneMap = defaultdict(list)

    for obj in objList:
        objByName[obj.name].append((obj, None))
        if obj.type == "ARMATURE":
            for b in obj.pose.bones:
                boneMap[b.name].append((obj, b.name))
    
    actionCache = {}

    offset = 0

    # Curve blocks
    importedCurves = [] #Index = curveId
    for _ in range(numCurves):
        keyCount, channelIndex, channelLen = struct.unpack_from('<iHH', buf, offset)
        offset += 8
        endOfName = offset + channelLen
        channel = buf[offset:endOfName].decode('utf-8')
        offset = endOfName
        keys = np.frombuffer(buf, dtype=structKeyblock, count=keyCount, offset=offset)
        offset += keyCount * structKeyblock.itemsize

        importedCurves.append({"channel":channel,"channelIndex":channelIndex,"keys":keys})

    # Object blocks
    for _ in range(numObjs):
        typeEnum, curveStart, curveCount, nameLen = struct.unpack_from('<BHHH', buf, offset)
        offset += 7
        endOfName = offset + nameLen
        name = buf[offset:endOfName].decode('utf-8')
        offset = endOfName

        isDataBlock = (typeEnum == objTypeEnum["OBJDATA"])

        splitName = name.split(':')
        if len(splitName) > 1:
            armName, boneName = splitName
            matches = [(objByName[armName][0][0], boneName)] if armName in objByName else []
        else:
            matches = objByName.get(name, []) + boneMap.get(name, [])

        if not matches:
            print(f"'{name}' not found. Skipping...")
            continue

        for obj, boneName in matches:
            curveDataList = importedCurves[curveStart : curveStart + curveCount]
            if boneName and not curveDataList[0]['channel'].startswith('pose.bones'):
                curveDataList = remapBoneChannels(curveDataList, boneName)

            target = obj.data if isDataBlock else obj
            cacheKey = f"{name}__data" if isDataBlock else name

            action, slot = getOrCreateAction(target, actionCache,cacheKey)
            applyFcurves(target, curveDataList, action, slot)

    return 0
