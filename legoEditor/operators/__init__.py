from . import select, move, rotate

modules = [select,move,rotate]

def register():
    for m in modules:
        m.register()

def unregister():
    for m in reversed(modules):
        m.unregister()
