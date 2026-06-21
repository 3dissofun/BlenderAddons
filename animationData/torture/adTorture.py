import bpy
import time

def setupScene(objCount,frames):
    sceneColl = bpy.context.collection
    print(f"Generating stress test scene with {objCount} objects, and {frames} frames.")
    for i in range(objCount):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        
        obj.animation_data_create()
        action = bpy.data.actions.new(name=f"Action_{i}")
        obj.animation_data.action = action

        for dataPath in ["location","rotation_euler","scale"]:
            for axis in range(3):
                fc = action.fcurve_ensure_for_datablock(obj, dataPath, index=axis)
                fc.keyframe_points.add(frames)

                coords = []
                for f in range(frames):
                    coords.extend([float(f),float(f*0.1)])
                
                fc.keyframe_points.foreach_set('co',coords)
                fc.update()
    print("Stress test scene setup!")
'''
def torture():
    setupScene(1000,1000)
    bpy.ops.object.select_all(action='SELECT')
    
    print("Starting Torture Test...")
    startTime = time.perf_counter()
    bpy.ops.export_scene.ad_animation()
    endTime = time.perf_counter()
    duration = endTime - startTime
    print(f"Torture test took: {duration} to complete.")
'''

if __name__ == "__main__":
    setupScene(1000,250)    
