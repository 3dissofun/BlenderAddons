import bpy

def getPref(prefName):
    preferences = bpy.context.preferences.addons[__package__].preferences
    return getattr(preferences, prefName, None)

