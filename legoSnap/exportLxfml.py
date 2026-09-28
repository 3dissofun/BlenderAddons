# Standalone exporter: writes the selected Lego bricks to an LXFML file (LEGO Digital Designer's XML format)
# Adds "Lego Model (.lxfml)" to File -> Export
#
# Usage from the addon's __init__.py:
#   from . import exportLxfml
#   exportLxfml.register()   /   exportLxfml.unregister()

import os
import re
import xml.etree.ElementTree as ET

import bpy
from bpy.props import StringProperty, IntProperty, BoolProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Matrix, Vector

# Blender is Z up. LDD is Y up (right handed), so this is the usual Z-up -> Y-up swap: (x, y, z) -> (x, z, -y)
BLtoLDD = Matrix(((1, 0, 0),
                  (0, 0, 1),
                  (0, -1, 0)))

# brickBuilder uses 0.02 Blender units per LDU. LDD uses 0.04 units per LDU (stud pitch 0.8), so the scale is x2
BL_TO_LDD_SCALE = 2.0

DEFAULT_MATERIAL = 1 # LDD "White"

# BRICK HELPERS

def isBrick(obj):
    if obj.type != 'MESH':
        return False
    if "lego_id" in obj:
        return True
    snaps = getattr(obj.data, "lego_snaps", None)
    return snaps is not None and len(snaps) > 0

def getBrickId(obj):
    # an explicit "lego_id" custom property wins, otherwise use the mesh name that brickBuilder set
    brickId = str(obj.get("lego_id") or obj.data.name)
    brickId = re.sub(r"\.\d{3,}$", "", brickId) # strip Blender's ".001" duplicate suffix
    if brickId.lower().endswith(".dat"):
        brickId = brickId[:-4]
    return brickId

def getMaterial(obj, fallback):
    # per brick override through a "lego_material" custom property (LDD material ID)
    try:
        return int(obj.get("lego_material", fallback))
    except (TypeError, ValueError):
        return fallback

def lddTransformation(matrixWorld, origin):
    # returns LDD's "a,b,c,d,e,f,g,h,i,x,y,z" string
    loc, rot, _ = matrixWorld.decompose() # drop object scale, LDD bricks can't be scaled
    r = BLtoLDD @ rot.to_matrix() @ BLtoLDD.transposed()
    p = BLtoLDD @ (loc - origin) * BL_TO_LDD_SCALE

    # LDD stores the rotation in row-vector form (v * M), which is the transpose of mathutils' column-vector form,
    # so write the columns of r one after another
    vals = [r[row][col] for col in range(3) for row in range(3)] + list(p)
    return ",".join(f"{v:.6f}".rstrip("0").rstrip(".") if abs(v) > 1e-9 else "0" for v in vals)

# XML BUILDING

def buildLxfml(bricks, modelName, defaultMaterial, centreModel):
    origin = Vector((0, 0, 0))
    if centreModel and bricks:
        # centre on the footprint, keep the lowest brick on the ground
        locs = [o.matrix_world.translation for o in bricks]
        origin = sum(locs, Vector()) / len(locs)
        origin.z = min(l.z for l in locs)

    root = ET.Element("LXFML", versionMajor="5", versionMinor="0", name=modelName)

    meta = ET.SubElement(root, "Meta")
    ET.SubElement(meta, "Application", name="LEGO Digital Designer", versionMajor="4", versionMinor="3")
    ET.SubElement(meta, "Brand", name="LDD")
    ET.SubElement(meta, "BrickSet", version="1264")

    cameras = ET.SubElement(root, "Cameras")
    ET.SubElement(cameras, "Camera", refID="0", fieldOfView="80", distance="50",
                  transformation="0.707107,0,-0.707107,-0.408248,0.816497,-0.408248,0.57735,0.57735,0.57735,28.8675,28.8675,28.8675")

    bricksEl = ET.SubElement(root, "Bricks", cameraRef="0")
    for refId, obj in enumerate(bricks):
        brickId = getBrickId(obj)
        brickEl = ET.SubElement(bricksEl, "Brick", refID=str(refId), designID=brickId)
        partEl = ET.SubElement(brickEl, "Part", refID=str(refId), designID=brickId,
                               materials=str(getMaterial(obj, defaultMaterial)))
        ET.SubElement(partEl, "Bone", refID=str(refId),
                      transformation=lddTransformation(obj.matrix_world, origin))

    ET.SubElement(root, "RigidSystems") # LDD rebuilds connections itself on load
    groups = ET.SubElement(root, "GroupSystems")
    ET.SubElement(groups, "BrickGroupSystem")
    ET.SubElement(root, "BuildingInstructions")

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    return tree

# OPERATOR

class LE_OT_ExportLxfml(bpy.types.Operator, ExportHelper):
    bl_idname = "lego.export_lxfml"
    bl_label = "Export Lego Model"
    bl_description = "Export the selected bricks as an LXFML (Lego XML) model"
    bl_options = {'REGISTER'}

    filename_ext = ".lxfml"
    filter_glob: StringProperty(default="*.lxfml", options={'HIDDEN'})

    defaultMaterial: IntProperty(
        name="Default Material",
        description="LDD material ID for bricks without a 'lego_material' property (1 = White, 21 = Bright Red, 23 = Bright Blue, 26 = Black)",
        default=DEFAULT_MATERIAL, min=0,
    )
    centreModel: BoolProperty(
        name="Centre Model",
        description="Move the model to the origin, standing on the ground",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return any(isBrick(o) for o in context.selected_objects)

    def execute(self, context):
        bricks = [o for o in context.selected_objects if isBrick(o)]
        skipped = len(context.selected_objects) - len(bricks)
        if not bricks:
            self.report({'WARNING'}, "No Lego bricks selected")
            return {'CANCELLED'}

        modelName = os.path.splitext(os.path.basename(self.filepath))[0]
        tree = buildLxfml(bricks, modelName, self.defaultMaterial, self.centreModel)
        try:
            tree.write(self.filepath, encoding="UTF-8", xml_declaration=True)
        except OSError as e:
            self.report({'ERROR'}, f"Could not write file: {e}")
            return {'CANCELLED'}

        msg = f"Exported {len(bricks)} bricks to {os.path.basename(self.filepath)}"
        if skipped:
            msg += f" ({skipped} non-brick objects skipped)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}

def menuExport(self, context):
    self.layout.operator(LE_OT_ExportLxfml.bl_idname, text="Lego Model (.lxfml)")

# REGISTRATION

classes = (LE_OT_ExportLxfml,)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_export.append(menuExport)

def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menuExport)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
