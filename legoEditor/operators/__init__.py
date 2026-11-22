from . import select, move, rotate, color

modules = [select,move,rotate,color]

def register():
    for m in modules:
        m.register()

def unregister():
    for m in reversed(modules):
        m.unregister()
