import bpy

from pathlib import Path

def getRepoDir():
    repoDir = bpy.context.preferences.addons[__package__].preferences.remoteDir
    return Path(repoDir)

def getLocalDir(name):
    localDir = Path(bpy.data.filepath).parent / ".bvc" / name
    localDir.mkdir(parents=True,exist_ok=True)
    return localDir
