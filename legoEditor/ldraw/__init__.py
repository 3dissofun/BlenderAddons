import bpy
from . import buildOps, buildProps, library
from .snaps import snapUiProps, snapUi
from . import pointCloud

def register():
    buildProps.register()
    buildOps.register()
    snapUiProps.register()
    snapUi.register()
    pointCloud.register()
    # Build the library after blender has loaded the main thread
    bpy.app.timers.register(library.startBackgroundBuild, first_interval=0.5)

def unregister():
    pointCloud.unregister()
    snapUi.unregister()
    snapUiProps.unregister()
    buildProps.unregister()
    buildOps.unregister()
