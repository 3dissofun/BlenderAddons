import bpy

def getPref(prefName):
    preferences = bpy.context.preferences.addons[__package__].preferences
    return getattr(preferences, prefName, None)

def setModifierInput(modifier, socketName, value):
    # small helper to set modifier input sockets by name
    identifier = next(item.identifier for item in modifier.node_group.interface.items_tree
        if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == socketName)
    getattr(modifier.properties.inputs, identifier).value = value

def getModifierInput(obj,modName,inputName):
    mod = obj.modifiers.get(modName,None)
    if not mod:
        print("LEGO EDITOR, ERROR: Asked to retrieve a value from a modifier that doesnt exist.")
        return None

    for item in mod.node_group.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == inputName:
            return mod[item.identifier]
    return None
