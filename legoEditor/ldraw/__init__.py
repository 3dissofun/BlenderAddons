from . import buildOps, buildProps, snapUiProps, snapUi

def register():
    buildProps.register()
    buildOps.register()
    snapUiProps.register()
    snapUi.register()

def unregister():
    snapUi.unregister()
    snapUiProps.unregister()
    buildProps.register()
    buildOps.unregister()
