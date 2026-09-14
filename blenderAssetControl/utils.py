import bpy

from pathlib import Path

def getRepoDir():
    repoDir = bpy.context.preferences.addons[__package__].preferences.remoteDir
    print(repoDir)
    return Path(repoDir)
