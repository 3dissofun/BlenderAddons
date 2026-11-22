import bpy

def isLegoPointCloud(obj):
    if obj is None:
        return False
    if obj.type == "POINTCLOUD" and getattr(obj,"is_lego_model",False):
        return True
    return False
