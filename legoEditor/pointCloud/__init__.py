from . import selectionOverlay, colorOverlay

modules = [selectionOverlay,]

def register():
    for m in modules:
        m.register()

def unregister():
    for m in reversed(modules):
        m.unregister()
