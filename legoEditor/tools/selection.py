import bpy
import gpu
import numpy as np
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

# Edges of Blender's 8-corner bound_box
BOX_EDGES = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
             (0, 4), (1, 5), (2, 6), (3, 7)]
HOVER_COL = (1.0, 0.55, 0.1, 1.0)
SELECT_COL = (1.0, 0.85, 0.2, 0.8)


class POINTCLOUD_OT_instance_picker(bpy.types.Operator):
    """Pick geo-nodes instances by bounding box and select their points"""
    bl_idname = "pointcloud.instance_picker"
    bl_label = "Instance Picker"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return (ob is not None and ob.type == 'POINTCLOUD'
                and context.area is not None and context.area.type == 'VIEW_3D')

    # ---- setup / teardown ----

    def invoke(self, context, event):
        self.obj = context.active_object
        self.region = next(r for r in context.area.regions if r.type == 'WINDOW')
        self.rv3d = context.area.spaces.active.region_3d
        self.shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        self.hover = None
        self.hover_batch = None
        self.sel_batch = None

        if not self.build_cache(context):
            self.report({'WARNING'}, "No instances found on this object")
            return {'CANCELLED'}

        self.handle = bpy.types.SpaceView3D.draw_handler_add(
            self.draw_boxes, (), 'WINDOW', 'POST_VIEW')
        self.rebuild_selected()
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def finish(self, context):
        if self.handle:
            bpy.types.SpaceView3D.draw_handler_remove(self.handle, 'WINDOW')
            self.handle = None
        context.area.tag_redraw()

    def build_cache(self, context):
        """Per instance: world matrix, local bounding box, source point index."""
        dg = context.evaluated_depsgraph_get()
        world, corners, points = [], [], []
        for di in dg.object_instances:
            if not di.is_instance or di.parent is None:
                continue
            if di.parent.original != self.obj:
                continue
            world.append(di.matrix_world.copy())
            corners.append([tuple(c) for c in di.object.bound_box])
            points.append(di.persistent_id[0])

        if not world:
            return False
        self.world = world
        self.corners = corners
        self.point_of = np.array(points)

        W = np.array([[list(row) for row in m] for m in world])
        self.Winv = np.linalg.inv(W)
        C = np.array(corners)                      # (N, 8, 3)
        self.bmin, self.bmax = C.min(axis=1), C.max(axis=1)
        return True

    # ---- picking: ray vs. every oriented box at once ----

    def mouse_coord(self, event):
        x, y = event.mouse_x - self.region.x, event.mouse_y - self.region.y
        if 0 <= x < self.region.width and 0 <= y < self.region.height:
            return (x, y)
        return None

    def pick(self, coord):
        o = view3d_utils.region_2d_to_origin_3d(self.region, self.rv3d, coord)
        d = view3d_utils.region_2d_to_vector_3d(self.region, self.rv3d, coord).normalized()

        lo = (self.Winv @ np.array([o.x, o.y, o.z, 1.0]))[:, :3]
        ld = self.Winv[:, :3, :3] @ np.array([d.x, d.y, d.z])
        with np.errstate(divide='ignore', invalid='ignore'):
            inv = 1.0 / ld
            t1 = (self.bmin - lo) * inv
            t2 = (self.bmax - lo) * inv
            t_near = np.nanmax(np.minimum(t1, t2), axis=1)
            t_far = np.nanmin(np.maximum(t1, t2), axis=1)

        hit = np.nonzero(t_far >= np.maximum(t_near, 0.0))[0]
        if len(hit) == 0:
            return None
        return int(hit[np.argmin(np.maximum(t_near[hit], 0.0))])

    # ---- drawing ----

    def box_lines(self, indices):
        coords = []
        for i in indices:
            M = self.world[i]
            c = [M @ Vector(p) for p in self.corners[i]]
            for a, b in BOX_EDGES:
                coords += (c[a], c[b])
        return coords

    def rebuild_selected(self):
        self.sel_batch = None
        sel = self.obj.data.attributes.get(".selection")
        if sel is None:
            return
        vals = np.zeros(len(sel.data),
                        dtype=np.float32 if sel.data_type == 'FLOAT' else bool)
        sel.data.foreach_get("value", vals)
        valid = self.point_of < len(vals)
        mask = np.zeros(len(self.point_of), dtype=bool)
        mask[valid] = vals[self.point_of[valid]].astype(bool)
        idx = np.nonzero(mask)[0]
        if len(idx):
            self.sel_batch = batch_for_shader(
                self.shader, 'LINES', {"pos": self.box_lines(idx)})

    def set_hover(self, i):
        if i == self.hover:
            return
        self.hover = i
        self.hover_batch = None if i is None else batch_for_shader(
            self.shader, 'LINES', {"pos": self.box_lines([i])})

    def _uniforms(self, color, width):
        vp = gpu.state.viewport_get()
        self.shader.bind()
        self.shader.uniform_float("viewportSize", (vp[2], vp[3]))
        self.shader.uniform_float("lineWidth", width)
        self.shader.uniform_float("color", color)

    def draw_boxes(self):
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        if self.sel_batch:
            self._uniforms(SELECT_COL, 1.5)
            self.sel_batch.draw(self.shader)
        if self.hover_batch:
            self._uniforms(HOVER_COL, 2.5)
            self.hover_batch.draw(self.shader)
        gpu.state.blend_set('NONE')

    # ---- selection ----

    def select(self, point, extend):
        pc = self.obj.data
        if not (0 <= point < len(pc.points)):
            return
        sel = pc.attributes.get(".selection")
        if sel is None:
            sel = pc.attributes.new(".selection", 'BOOLEAN', 'POINT')
        on, off = (1.0, 0.0) if sel.data_type == 'FLOAT' else (True, False)
        if extend:
            sel.data[point].value = off if sel.data[point].value else on
        else:
            sel.data.foreach_set("value", [off] * len(sel.data))
            sel.data[point].value = on
        pc.update_tag()
        self.rebuild_selected()

    # ---- event loop ----

    def modal(self, context, event):
        try:
            if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                self.finish(context)
                return {'FINISHED'}

            coord = self.mouse_coord(event)
            if coord is None:
                return {'PASS_THROUGH'}

            if event.type == 'MOUSEMOVE':
                self.set_hover(self.pick(coord))
                context.area.tag_redraw()

            elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                if self.hover is not None:
                    self.select(int(self.point_of[self.hover]), event.shift)
                    context.area.tag_redraw()

            elif event.type == 'R' and event.value == 'PRESS':
                self.build_cache(context)
                self.hover = None
                self.set_hover(self.pick(coord))
                self.rebuild_selected()
                context.area.tag_redraw()

            elif event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
                                'TRACKPADPAN', 'TRACKPADZOOM'} or event.type.startswith('NUMPAD'):
                return {'PASS_THROUGH'}

        except Exception:
            self.finish(context)
            raise
        return {'RUNNING_MODAL'}


class VIEW3D_PT_instance_picker(bpy.types.Panel):
    bl_label = "Instance Picker"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Instances"

    def draw(self, context):
        layout = self.layout
        ob = context.active_object
        if ob is None or ob.type != 'POINTCLOUD':
            layout.label(text="Select a point cloud object", icon='INFO')
            return
        layout.operator(POINTCLOUD_OT_instance_picker.bl_idname,
                        text="Pick Instances", icon='RESTRICT_SELECT_OFF')
        col = layout.column(align=True)
        col.scale_y = 0.8
        col.label(text="LMB: select  /  Shift+LMB: toggle")
        col.label(text="R: refresh  /  Esc, RMB: exit")


classes = (POINTCLOUD_OT_instance_picker, VIEW3D_PT_instance_picker)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()