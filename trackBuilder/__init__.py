import bpy
import math
from mathutils import Vector, Matrix, Euler
from mathutils.geometry import intersect_line_plane
from bpy_extras import view3d_utils

bl_info = {
    "name": "Lego Track",
    "author": "Joshua Palfrey",
    "version": (1, 0, 0),
    "blender": (5, 2, 0),
    "location": "3dView -> Track",
    "description": ("Track building tool for lego"),
    "category": "Modelling",
}
 
def screenToWorldPos(context,screenCoord):
    # Returns a coord on the xy plane given a screen space coordinate input.
    region = context.region
    data3d = context.region_data

    # Ray through mouse pos
    rayOrigin = view3d_utils.region_2d_to_origin_3d(region,data3d,screenCoord)
    rayDir = view3d_utils.region_2d_to_vector_3d(region,data3d,screenCoord)

# XY plane definition (Could replace later with terrain?)
    planeOrigin = Vector((0,0,0))
    planeNormal = Vector((0,0,1))
    
    hitCoord = intersect_line_plane(rayOrigin,rayOrigin+rayDir,planeOrigin,planeNormal)
    return hitCoord

def getPieceMap(piecesCollection):
    trackPieces = piecesCollection.objects # List of objects named based on their angle
    print(f"LEGO TRACK: INFO, Found pieces {trackPieces}")
    straightPiece = trackPieces.get("0")
    if not straightPiece:
        print("LEGO TRACK: ERROR, No straight piece named '0' found, cannot build track.")
        return {}
    # Angles to pieces
    anglesToPieces = {} # Map of integer angles to piece objects
    for element in trackPieces:
        name = element.name
        try:
            angleName = int(name)
            anglesToPieces[angleName] = element
        except ValueError:
            if name.lower().startswith("end"):
                continue
            print(f"LEGO TRACK: WARNING, Invalid naming on object '{name}' needs to be an integer angle.")
            continue
    return anglesToPieces

def getEndPos(piece):
    # given an object with an empty named "End" as a child of it, return the empties position
    for o in piece.children:
        if o.type == "EMPTY" and o.name.lower().startswith("end"):
            return o.matrix_local.translation.copy()
    print(f"LEGO TRACK: WARNING, '{piece.name}' is mising an 'end' empty!")
    return Vector((0,0,0))

def normalizeAngle(angle):
    # Takes an angle and maps it to <180
    a = angle % 360.0
    if a > 180.0:
        a -= 360.0
    return a

class LT_OT_DrawTrack(bpy.types.Operator):
    bl_idname = "lt.draw_track"
    bl_label = "Draw Track"
    bl_options = {"REGISTER", "UNDO"}
 
    def snapAngle(self, turnAngle):
        # Function that takes in an angle and snaps it to the closest available in anglesToPieces
        available = list(self.anglesToPieces.keys())
        mirrored = [-a for a in available if a != 0]
        candidates = set(available) | set(mirrored)
        return min(candidates, key=lambda a: abs(normalizeAngle(a - turnAngle)))

    def getPiece(self, turnAngle):
        # returns a piece given an angle accounting for negative anfgles as mirrors
        # of their positive versions
        piece = self.anglesToPieces.get(turnAngle)
        if piece:
            return piece,False
        piece = self.anglesToPieces.get(-turnAngle)
        if piece:
            return piece,True
        return None,False

    def realisePointData(self):
        # Convert pointData list into real points
        positions = [p[0] for p in self.pointData]
        rotations = [p[1] for p in self.pointData]
        pieceType = [p[2] for p in self.pointData]

        pointMesh = bpy.data.meshes.new("TrackPoints")
        pointObj = bpy.data.objects.new("TrackPoints",pointMesh) 
        self.collection.objects.link(pointObj)
        pointMesh.from_pydata(positions,[],[])

        rotAttr = pointMesh.attributes.new(name="rot",type='FLOAT',domain='POINT')
        rotAttr.data.foreach_set("value",rotations)

        pieceAttr = pointMesh.attributes.new(name="type",type='INT',domain='POINT')
        pieceAttr.data.foreach_set("value",pieceType)
        pointMesh.update()

    def invoke(self, context, event):
        # Make a new collection each time operator is run 
        self.collection = bpy.data.collections.new("Track")
        bpy.context.scene.collection.children.link(self.collection)

        # Set up pieces
        piecesCollection = bpy.data.collections.get("pieces")
        if not piecesCollection:
            self.report({'ERROR'},"No track pieces collection given.")
            return {'CANCELLED'}

        self.anglesToPieces = getPieceMap(piecesCollection)
        if not self.anglesToPieces:
            self.report({'ERROR'}, "No valid angle-named pieces found in 'pieces' collection.")
            return {'CANCELLED'}

        # Core
        self.lastPosition = None
        self.position = context.scene.cursor.location.copy() # Where are we?
        self.lastHeading = None
        self.heading = 0 
        self.isStart = True # Is the start of the track building
        self.previewObj = None

        self.pointData = [] # list of tuples of point data/attributes

        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Track: aim + click to place, right-click to undo, ESC to finish")

        return {"RUNNING_MODAL"}
 
    def modal(self, context, event):
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}
 
        if event.type == "MOUSEMOVE":
            targetPos = screenToWorldPos(context,(event.mouse_region_x,event.mouse_region_y))
            direction = self.position - targetPos
            self.pickAngle = math.degrees(math.atan2(direction.y,direction.x))
            
            if self.isStart:
                if not self.previewObj:
                    straightPiece = self.anglesToPieces.get(0)
                    self.previewObj = straightPiece.copy()
                    self.previewObj.data = straightPiece.data
                    self.previewObj.name = "PREVIEW"
                    self.previewObj.display_type = "WIRE"
                    self.previewObj.hide_select = True
                    self.previewObj.location = self.position
                    self.collection.objects.link(self.previewObj)
                
                self.previewObj.rotation_euler.z = math.radians(self.pickAngle)
                
            else:
                desiredTurn = normalizeAngle(self.pickAngle - self.heading)
                snappedAngle = self.snapAngle(desiredTurn)
                nextPiece, mirrored = self.getPiece(snappedAngle)
                if not nextPiece:
                    self.report({'ERROR'},f"Failed to find piece for snapped angle '{snappedAngle}'")
                    return {"CANCELLED"}

                if self.previewObj.data is None and nextPiece.data is None:
                    if self.previewObj.instance_collection != nextPiece.instance_collection:
                        self.previewObj.instance_collection = nextPiece.instance_collection
                else:
                    if self.previewObj.data != nextPiece.data:
                        self.previewObj.data = nextPiece.data

                self.previewObj.location = self.position
                self.previewObj.rotation_euler.z = math.radians(self.heading)
                self.previewObj.scale.y = -1 if mirrored else 1
                context.area.header_text_set(f"Next turn angle: {snappedAngle}")

        elif event.type == "LEFTMOUSE" and event.value == "PRESS":
            if self.isStart:
                self.isStart = False
                self.heading = normalizeAngle(self.pickAngle)
                snappedAngle = 0
                sourcePiece, mirrored = self.getPiece(0)
            else:
                snappedAngle = self.snapAngle(normalizeAngle(self.pickAngle - self.heading))
                sourcePiece, mirrored = self.getPiece(snappedAngle)

            if not sourcePiece:
                self.report({'ERROR'},f"Failed to find piece for snapped angle '{snappedAngle}'")
                return {"CANCELLED"}

            self.pointData.append((self.position.copy(),math.radians(self.heading),snappedAngle))
            placePiece = sourcePiece.copy()
            placePiece.data = sourcePiece.data
            placePiece.location = self.position
            placePiece.rotation_euler.z = math.radians(self.heading)
            placePiece.scale.y *= -1 if mirrored else 1
            self.collection.objects.link(placePiece)

            localEnd = getEndPos(sourcePiece)
            rotatedEnd = localEnd.copy()
            if mirrored:
                rotatedEnd.y *= -1
            rotatedEnd.rotate(Euler((0, 0, math.radians(self.heading))))

            self.lastPosition
            self.position += rotatedEnd
            self.heading = normalizeAngle(self.heading + snappedAngle)
            return {"RUNNING_MODAL"}
 
        elif event.type in {"RET","ESC", "NUMPAD_ENTER"}:
            context.area.header_text_set(None)
            self.realisePointData()
            if self.previewObj:
                bpy.data.objects.remove(self.previewObj,do_unlink=True)
            return {"FINISHED"}
 
        return {"RUNNING_MODAL"}
 
class VIEW3D_PT_LegoTrack(bpy.types.Panel):
    bl_label = "Track Tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Lego Track"
 
    def draw(self, context):
        layout = self.layout
        layout.operator("lt.draw_track", text="Draw Track", icon="GREASEPENCIL")
 
classes = (VIEW3D_PT_LegoTrack, LT_OT_DrawTrack,)
 
def register():
    for cls in classes:
        bpy.utils.register_class(cls)
 
def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)
