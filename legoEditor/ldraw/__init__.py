from . import buildOps, buildProps 

def register():
    buildProps.register()
    buildOps.register()

def unregister():
    buildProps.register()
    buildOps.unregister()
