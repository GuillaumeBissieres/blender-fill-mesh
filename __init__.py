# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

bl_info = {
    "name": "Fill Mesh",
    "author": "Guillaume Bissieres",
    "description": "An add-on to fix and fill hole on your meshes",
    "blender": (3, 0, 0),
    "version": (1, 0, 6),
    "location": "View3D > Sidebar (N) > Fill Mesh",
    "warning" : "",
    "doc_url": "https://bissieres.gumroad.com/l/FillMesh", 
    "tracker_url": "https://github.com/GuillaumeBissieres/blender-fill-mesh", 
    "category" : "3D View" 
}

import bpy
import bmesh
from mathutils import Vector, Matrix
from typing import List, Set, Tuple
import math
import traceback
from bpy.props import IntProperty

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def get_bmesh_for_object(obj: bpy.types.Object, edit_ok: bool = True) -> Tuple[bmesh.types.BMesh, bool]:
    if obj is None:
        raise ValueError("No object provided")
    if obj.type != 'MESH':
        raise TypeError("Object is not a mesh")
    mesh = obj.data
    if edit_ok and obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(mesh)
        return bm, True
    else:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        return bm, False


def release_bmesh(obj: bpy.types.Object, bm: bmesh.types.BMesh, is_edit: bool):
    mesh = obj.data
    if is_edit:
        bmesh.update_edit_mesh(mesh)
    else:
        bm.to_mesh(mesh)
        mesh.update()
        bm.free()


def find_boundary_edges(bm: bmesh.types.BMesh) -> List[bmesh.types.BMEdge]:
    return [e for e in bm.edges if not e.is_manifold]


def loops_from_boundary_edges(boundary_edges: List[bmesh.types.BMEdge]) -> List[List[bmesh.types.BMEdge]]:
    loops = []
    visited = set()
    vert_to_edges = {}
    for e in boundary_edges:
        for v in e.verts:
            vert_to_edges.setdefault(v, []).append(e)

    for e in boundary_edges:
        if e in visited:
            continue
        loop = [e]
        visited.add(e)
        current = e
        while True:
            next_edge = None
            for v in current.verts:
                for cand in vert_to_edges.get(v, []):
                    if cand not in visited and cand is not current:
                        next_edge = cand
                        break
                if next_edge:
                    break
            if not next_edge:
                break
            loop.append(next_edge)
            visited.add(next_edge)
            current = next_edge
        loops.append(loop)
    return loops


def fill_loop_with_triangle_fill(bm: bmesh.types.BMesh, loop: List[bmesh.types.BMEdge]) -> bool:
    try:
        res = bmesh.ops.triangle_fill(bm, edges=loop, use_beauty=True)
    except Exception:
        res = None
    faces = res.get('faces', []) if res else []
    return bool(faces)


def fill_loop_with_holes_fill(bm: bmesh.types.BMesh, loop: List[bmesh.types.BMEdge]) -> List[bmesh.types.BMFace]:
    try:
        res = bmesh.ops.holes_fill(bm, edges=loop, sides=len(loop))
    except Exception:
        res = None
    faces = res.get('faces', []) if res else []
    return faces


def smooth_vertices(bm: bmesh.types.BMesh, verts: Set[bmesh.types.BMVert], factor: float = 1.0):
    if not verts:
        return
    try:
        bmesh.ops.smooth_vert(bm, verts=list(verts), factor=factor,
                              use_axis_x=True, use_axis_y=True, use_axis_z=True)
    except Exception:
        pass


def preserve_mode_context(context):
    try:
        return context.mode
    except Exception:
        return None


def restore_mode_context(prev_mode):
    try:
        if prev_mode and bpy.context.object is not None and bpy.context.mode != prev_mode:
            bpy.ops.object.mode_set(mode=prev_mode)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# LoopCut utilities (no debug subpanel)
# ---------------------------------------------------------------------------

def find_mesh_edge_index_from_bmedge(mesh, bm_edge):
    try:
        v1 = bm_edge.verts[0].index
        v2 = bm_edge.verts[1].index
    except Exception:
        return None
    for i, e in enumerate(mesh.edges):
        if (e.vertices[0] == v1 and e.vertices[1] == v2) or (e.vertices[0] == v2 and e.vertices[1] == v1):
            return i
    return None

def get_selected_edge_index(context):
    if context.mode != 'EDIT_MESH':
        return None
    obj = context.object
    if obj is None or obj.type != 'MESH':
        return None
    mesh = obj.data
    try:
        bm = bmesh.from_edit_mesh(mesh)
    except Exception:
        return None
    bm.edges.ensure_lookup_table()
    for e in bm.edges:
        if e.select:
            return find_mesh_edge_index_from_bmedge(mesh, e)
    return None

# Wrapper operator to reliably invoke the modal Loop Cut operator in a proper VIEW_3D region
# ---------------------------------------------------------------------------
# OpenQuad: Boundary Quad Propagation (replaces previous Simple Quad Fill)
# ---------------------------------------------------------------------------

def pick_shortest_boundary_edge(edges):
    if len(edges) != 2:
        return edges

    e1, e2 = edges
    shared = list(set(e1.verts) & set(e2.verts))
    if not shared:
        return []

    v = shared[0]
    d1 = (e1.other_vert(v).co - v.co).length
    d2 = (e2.other_vert(v).co - v.co).length

    chosen = e1 if d1 <= d2 else e2
    e1.select = e2.select = False
    chosen.select = True
    return [chosen]


def is_growth_edge(edge, seed_edge, reference_face, max_angle):
    if len(edge.link_faces) != 1:
        return False

    if reference_face and edge.link_faces[0] == reference_face:
        loop = next((l for l in reference_face.loops if l.edge == edge), None)
        if not loop or loop.is_convex:
            return False
        return loop.calc_angle() < math.pi - math.radians(5)

    common = set(edge.verts) & set(seed_edge.verts)
    if not common:
        return False

    anchor = common.pop()
    a = seed_edge.other_vert(anchor).co - anchor.co
    b = edge.other_vert(anchor).co - anchor.co

    return (
        a.length > 0 and
        b.length > 0 and
        a.angle(b) <= math.pi - math.radians(max_angle)
    )


def orient_new_face(face, reference):
    face.normal_update()
    if reference and face.normal.dot(reference.normal) < 0:
        face.normal_flip()


def opposite_quad_edge(face, reference_edge):
    if len(face.edges) != 4:
        return None
    for e in face.edges:
        if e != reference_edge and not (set(e.verts) & set(reference_edge.verts)):
            return e
    return None


def next_boundary_edge(vertex, excluded):
    for e in vertex.link_edges:
        if e not in excluded and len(e.link_faces) == 1:
            return e
    return None


def evaluate_growth_edge(edge, seed_edge, anchor_vert, reference_face):
    v1 = edge.other_vert(anchor_vert).co - anchor_vert.co
    v2 = seed_edge.other_vert(anchor_vert).co - anchor_vert.co

    try:
        angle_score = v1.angle(v2) / math.pi
    except:
        angle_score = 1.0

    length_score = abs(v1.length - v2.length) / max(v2.length, 1e-6)
    normal_score = abs(v1.normalized().dot(reference_face.normal)) if reference_face else 0.0

    return angle_score + 0.5 * length_score + 0.75 * normal_score


def find_corner_candidate(anchor, source_vert, via_vert, direction, max_angle=25):
    best = None
    best_score = None

    for e in via_vert.link_edges:
        if len(e.link_faces) != 1:
            continue

        v = e.other_vert(via_vert)
        if v in (anchor, source_vert):
            continue

        vec = v.co - anchor.co
        try:
            ang = vec.angle(direction)
        except:
            continue

        if ang > math.radians(max_angle):
            continue

        score = ang + vec.length * 0.1
        if best_score is None or score < best_score:
            best = v
            best_score = score

    return best


def find_alignment_candidate(anchor, source_vert, direction, max_angle=25):
    best = None
    best_score = None

    for e in source_vert.link_edges:
        if len(e.link_faces) != 1:
            continue

        v = e.other_vert(source_vert)
        if v == anchor:
            continue

        vec = v.co - anchor.co
        try:
            ang = vec.angle(direction)
        except:
            continue

        if ang > math.radians(max_angle):
            continue

        score = ang + vec.length * 0.1
        if best_score is None or score < best_score:
            best = v
            best_score = score

    return best


def propagate_quad_from_boundary(bm, flat_limit=45.0):
    selected = [e for e in bm.edges if e.select and len(e.link_faces) <= 1]

    if len(selected) == 2:
        selected = pick_shortest_boundary_edge(selected)

    if len(selected) != 1:
        return None

    seed_edge = selected[0]
    seed_edge.select = False
    reference_face = seed_edge.link_faces[0] if len(seed_edge.link_faces) == 1 else None

    vA, vB = seed_edge.verts
    candidates = []

    for anchor in (vA, vB):
        for e in anchor.link_edges:
            if e != seed_edge and is_growth_edge(e, seed_edge, reference_face, flat_limit):
                score = evaluate_growth_edge(e, seed_edge, anchor, reference_face)
                candidates.append((score, e, anchor))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    _, growth_edge, anchor = candidates[0]

    source_vert = seed_edge.other_vert(anchor)
    via_vert = growth_edge.other_vert(anchor)

    dir_vec = (source_vert.co - anchor.co) + (via_vert.co - anchor.co)
    if not dir_vec.length:
        return None
    dir_vec.normalize()

    merged = False
    corner = find_corner_candidate(anchor, source_vert, via_vert, dir_vec)

    if not corner:
        corner = find_alignment_candidate(anchor, source_vert, dir_vec)

    if corner:
        merged = True
    else:
        corner = bm.verts.new(anchor.co + (source_vert.co - anchor.co) + (via_vert.co - anchor.co))

    geom = list(dict.fromkeys([corner, source_vert, anchor, via_vert]))
    if len(geom) < 4:
        return None

    res = bmesh.ops.contextual_create(bm, geom=geom)
    faces = res.get("faces", [])
    if not faces:
        return None

    face = faces[0]
    orient_new_face(face, reference_face)

    next_edge = (
        next_boundary_edge(corner, {seed_edge, growth_edge})
        if merged
        else opposite_quad_edge(face, seed_edge)
    )

    if next_edge:
        for e in bm.edges:
            e.select = False
        next_edge.select = True

    return face


class FILLMESH_OT_simple_quad_fill(bpy.types.Operator):
    bl_idname = "fillmesh.simple_quad_fill"
    bl_label = "Simple Quad Fill"
    bl_description = "OpenQuad - boundary driven quad propagation"
    bl_options = {"REGISTER", "UNDO"}

    repeat: IntProperty(name="Repeat", default=1, min=1, max=128)

    @classmethod
    def poll(cls, context):
        return (
            context.active_object and
            context.active_object.type == "MESH" and
            context.mode == "EDIT_MESH"
        )

    def execute(self, context):
        bm = bmesh.from_edit_mesh(context.active_object.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        for _ in range(self.repeat):
            if not propagate_quad_from_boundary(bm):
                break

        bmesh.update_edit_mesh(context.active_object.data)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Addon Preferences
# ---------------------------------------------------------------------------

class FillMeshPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    merge_threshold: bpy.props.FloatProperty(
        name="Merge Threshold",
        default=0.015,
        min=0.0,
        max=1.0,
        description="Distance threshold used by Merge Vertex Group / Snap Vertex"
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="Fill Mesh Addon Preferences")
        layout.prop(self, "merge_threshold")


# ---------------------------------------------------------------------------
# Helper to provide dynamic EnumProperty items for holes
# ---------------------------------------------------------------------------

def get_holes_enum(self, context):
    items = []
    obj = context.object
    if obj is None or obj.type != 'MESH':
        return items
    need_free = False
    try:
        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            need_free = False
        else:
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            need_free = True
        boundary_edges = [e for e in bm.edges if not e.is_manifold]
        if not boundary_edges:
            return items
        loops = loops_from_boundary_edges(boundary_edges)
        items.append(('ALL', "All holes", "Select all detected holes"))
        for i, loop in enumerate(loops):
            items.append((str(i), f"Hole {i+1} ({len(loop)} edges)", f"Boundary loop with {len(loop)} edges"))
    except Exception:
        items = []
    finally:
        if need_free and 'bm' in locals():
            try:
                bm.free()
            except Exception:
                pass
    return items


# ---------------------------------------------------------------------------
# UI Panel (no colored boxes)
# ---------------------------------------------------------------------------
class FILLMESH_PT_main_panel(bpy.types.Panel):
    bl_label = "Fill Mesh"
    bl_idname = "FILLMESH_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Fill Mesh"

    @classmethod
    def poll(cls, context):
        return True

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Fill & Bridge section (no box)
        row = layout.row(align=True)
        icon = 'TRIA_DOWN' if getattr(scene, "fillmesh_section_fillbridge", True) else 'TRIA_RIGHT'
        row.prop(scene, "fillmesh_section_fillbridge", icon=icon, emboss=False, text="Fill & Bridge")
        if scene.fillmesh_section_fillbridge:
            # two buttons side-by-side
            sub = layout.row(align=True)
            sub.operator_context = "INVOKE_DEFAULT"
            sub.operator("fillmesh.simple_bridge_fill", text="Simple Bridge Fill")
            sub.operator("mesh.fill_grid", text="Smart Fill Select")
            layout.separator()
            layout.operator("fillmesh.detect_hole", text="Detect Hole")

        # Fill Tools & Utilities (no box)
        row = layout.row(align=True)
        icon = 'TRIA_DOWN' if getattr(scene, "fillmesh_section_tools", True) else 'TRIA_RIGHT'
        row.prop(scene, "fillmesh_section_tools", icon=icon, emboss=False, text="Fill Tools & Utilities")
        if scene.fillmesh_section_tools:
            grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=True)
            grid.operator("fillmesh.snap_vertex", text="Snap Vertex")
            grid.operator("fillmesh.merge_vertex_group", text="Merge Vertex Group")
            grid.operator("fillmesh.fill_mesh", text="Fill Mesh")
            grid.operator("fillmesh.fill_mesh_select", text="Fill Mesh Select")
            grid.operator("fillmesh.fill_shape", text="Fill Shape")
            grid.operator("fillmesh.fill_shape_select", text="Fill Shape Select")
            layout.separator()
            layout.operator("fillmesh.simple_quad_fill", text="Simple Quad Fill", icon='MESH_PLANE')


# ---------------------------------------------------------------------------
# Detect Hole operator (improved selection persisted as edges + verts)
# ---------------------------------------------------------------------------
class FILLMESH_OT_detect_hole(bpy.types.Operator):
    bl_idname = "fillmesh.detect_hole"
    bl_label = "Detect Hole"
    bl_description = "Detect and select damaged vertices and holes on active object (choose hole in Adjust Last Operation)"
    bl_options = {"REGISTER", "UNDO"}

    hole: bpy.props.EnumProperty(
        name="Hole",
        description="Select which hole to target (choose 'All holes' to select everything)",
        items=get_holes_enum
    )

    single_hole_mode: bpy.props.BoolProperty(
        name="Single Hole Mode",
        description="Select only one hole corresponding to the slider value",
        default=False
    )

    radius: bpy.props.IntProperty(
        name="Number of Holes",
        default=1,
        min=1,
        max=100
    )

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH'

    def execute(self, context):
        obj = context.object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Active object must be a mesh")
            return {'CANCELLED'}

        prev_mode = preserve_mode_context(context)
        try:
            # Keep working in Edit Mode (we need edit bmesh)
            if context.mode != 'EDIT_MESH':
                bpy.ops.object.mode_set(mode='EDIT')

            # ensure vertex mode to present selection visually in verts first
            try:
                bpy.ops.mesh.select_mode(type='VERT')
            except Exception:
                pass

            mesh = obj.data
            bm = bmesh.from_edit_mesh(mesh)

            try:
                bm.select_mode |= {'VERT'}
                bm.select_flush(True)
            except Exception:
                pass

            boundary_edges = [e for e in bm.edges if not e.is_manifold]
            if not boundary_edges:
                self.report({'INFO'}, "No holes detected")
                return {'FINISHED'}

            # Build loops robustly
            boundary_loops = []
            visited_edges = set()
            for edge in boundary_edges:
                if edge in visited_edges:
                    continue
                loop = []
                current = edge
                while current and current not in visited_edges:
                    visited_edges.add(current)
                    loop.append(current)
                    next_edges = [e for e in boundary_edges if e not in visited_edges and (e.verts[0] in current.verts or e.verts[1] in current.verts)]
                    current = next_edges[0] if next_edges else None
                if len(loop) > 0:
                    boundary_loops.append(loop)

            
            chosen = getattr(self, "hole", None)

            # Sort holes by size (largest first)
            boundary_loops.sort(key=lambda loop: len(loop), reverse=True)

            # Slider modes
            if hasattr(self, "radius") and self.radius > 0:

                if getattr(self, "single_hole_mode", False):

                    idx = min(max(self.radius - 1, 0), max(len(boundary_loops) - 1, 0))

                    if boundary_loops:
                        boundary_loops = [boundary_loops[idx]]

                else:

                    boundary_loops = boundary_loops[:min(self.radius, len(boundary_loops))]

            # Select all loops: select both edges and their verts
            if chosen in (None, "", "ALL"):
                # clear previous selection (verts + edges)
                try:
                    for v in bm.verts:
                        v.select = False
                    for e in bm.edges:
                        e.select = False
                except Exception:
                    pass

                count = 0
                for loop in boundary_loops:
                    for edge in loop:
                        try:
                            edge.select = True
                            edge.verts[0].select = True
                            edge.verts[1].select = True
                        except Exception:
                            pass
                    count += 1

                bmesh.update_edit_mesh(mesh)
                # refresh 3D views
                for window in context.window_manager.windows:
                    for area in window.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.tag_redraw()
                self.report({'INFO'}, f"Detected and selected {count} hole(s)")
                return {'FINISHED'}

            # Specific hole chosen
            try:
                idx = int(chosen)
            except Exception:
                self.report({'ERROR'}, "Invalid hole selection")
                return {'CANCELLED'}

            if idx < 0 or idx >= len(boundary_loops):
                self.report({'ERROR'}, "Selected hole index out of range")
                return {'CANCELLED'}

            # Clear vertex and edge selection, then select edges of the chosen loop (and their verts)
            try:
                for v in bm.verts:
                    v.select = False
                for e in bm.edges:
                    e.select = False
            except Exception:
                pass

            chosen_loop = boundary_loops[idx]
            for edge in chosen_loop:
                try:
                    edge.select = True
                    edge.verts[0].select = True
                    edge.verts[1].select = True
                except Exception:
                    pass

            bmesh.update_edit_mesh(mesh)
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()

            self.report({'INFO'}, f"Detected and selected Hole {idx+1}")
            return {'FINISHED'}
        finally:
            restore_mode_context(prev_mode)


# ---------------------------------------------------------------------------
# Other Operators (kept unchanged)
# ---------------------------------------------------------------------------

class FILLMESH_OT_fill_mesh(bpy.types.Operator):
    bl_idname = "fillmesh.fill_mesh"
    bl_label = "Fill Mesh"
    bl_description = "Fix and fill holes (object mode)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH'

    def execute(self, context):
        prev_mode = preserve_mode_context(context)
        try:
            obj = context.object
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')

            try:
                bm, is_edit = get_bmesh_for_object(obj, edit_ok=False)
            except Exception as e:
                self.report({'ERROR'}, f"Cannot access mesh: {e}")
                return {'CANCELLED'}

            try:
                boundary_edges = find_boundary_edges(bm)
                if not boundary_edges:
                    self.report({'INFO'}, "No boundary edges (holes) found")
                    if not is_edit:
                        bm.free()
                    return {'FINISHED'}

                boundary_loops = loops_from_boundary_edges(boundary_edges)
                count = 0
                for loop in boundary_loops:
                    created = fill_loop_with_triangle_fill(bm, loop)
                    if not created:
                        faces = fill_loop_with_holes_fill(bm, loop)
                        if faces:
                            try:
                                bmesh.ops.triangulate(bm, faces=faces)
                            except Exception:
                                pass
                    count += 1
                release_bmesh(obj, bm, is_edit)
                self.report({'INFO'}, f"Holes processed: {count}")
                return {'FINISHED'}
            finally:
                pass
        finally:
            restore_mode_context(prev_mode)


class FILLMESH_OT_fill_shape(bpy.types.Operator):
    bl_idname = "fillmesh.fill_shape"
    bl_label = "Fill Shape"
    bl_description = "Calculate shape, fix and fill holes"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH'

    def execute(self, context):
        prev_mode = preserve_mode_context(context)
        try:
            obj = context.object
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')

            try:
                bm, is_edit = get_bmesh_for_object(obj, edit_ok=False)
            except Exception as e:
                self.report({'ERROR'}, f"Cannot access mesh: {e}")
                return {'CANCELLED'}

            try:
                boundary_edges = find_boundary_edges(bm)
                if not boundary_edges:
                    self.report({'INFO'}, "No boundary edges found")
                    if not is_edit:
                        bm.free()
                    return {'FINISHED'}

                boundary_loops = loops_from_boundary_edges(boundary_edges)
                for loop in boundary_loops:
                    verts = set()
                    for e in loop:
                        verts.update(e.verts)
                    faces = fill_loop_with_holes_fill(bm, loop)
                    if faces:
                        try:
                            bmesh.ops.triangulate(bm, faces=faces)
                        except Exception:
                            pass
                    smooth_vertices(bm, verts, factor=1.0)

                release_bmesh(obj, bm, is_edit)
                self.report({'INFO'}, "Fill Shape: Completed")
                return {'FINISHED'}
            finally:
                pass
        finally:
            restore_mode_context(prev_mode)


class FILLMESH_OT_fill_mesh_select(bpy.types.Operator):
    bl_idname = "fillmesh.fill_mesh_select"
    bl_label = "Fill Mesh Select"
    bl_description = "Fix and fill hole using selected boundary (Edit Mode)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH' and context.mode == 'EDIT_MESH'

    def execute(self, context):
        prev_mode = preserve_mode_context(context)
        try:
            obj = context.object
            if context.mode != 'EDIT_MESH':
                bpy.ops.object.mode_set(mode='EDIT')

            bm = bmesh.from_edit_mesh(obj.data)
            try:
                bpy.ops.mesh.select_mode(type='VERT')
            except Exception:
                pass

            boundary_edges = [e for e in bm.edges if not e.is_manifold and e.verts[0].select and e.verts[1].select]
            if not boundary_edges:
                self.report({'INFO'}, "No boundary edges in selection")
                return {'FINISHED'}

            loops = loops_from_boundary_edges(boundary_edges)
            for loop in loops:
                created = fill_loop_with_triangle_fill(bm, loop)
                if not created:
                    faces = fill_loop_with_holes_fill(bm, loop)
                    if faces:
                        bmesh.ops.triangulate(bm, faces=faces)

            bmesh.update_edit_mesh(obj.data)
            self.report({'INFO'}, "Selected holes processed")
            return {'FINISHED'}
        finally:
            restore_mode_context(prev_mode)


class FILLMESH_OT_fill_shape_select(bpy.types.Operator):
    bl_idname = "fillmesh.fill_shape_select"
    bl_label = "Fill Shape Select"
    bl_description = "Fill selection with computed shape"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH' and context.mode == 'EDIT_MESH'

    def execute(self, context):
        prev_mode = preserve_mode_context(context)
        try:
            obj = context.object
            bm = bmesh.from_edit_mesh(obj.data)
            boundary_edges = [e for e in bm.edges if not e.is_manifold and e.verts[0].select and e.verts[1].select]
            if not boundary_edges:
                self.report({'INFO'}, "No boundary edges in selection")
                return {'FINISHED'}
            loops = loops_from_boundary_edges(boundary_edges)
            for loop in loops:
                verts = set()
                for e in loop:
                    verts.update(e.verts)
                faces = fill_loop_with_holes_fill(bm, loop)
                if faces:
                    try:
                        bmesh.ops.triangulate(bm, faces=faces)
                    except Exception:
                        pass
                smooth_vertices(bm, verts, factor=1.0)
            bmesh.update_edit_mesh(obj.data)
            self.report({'INFO'}, "Fill shape on selection completed")
            return {'FINISHED'}
        finally:
            restore_mode_context(prev_mode)


class FILLMESH_OT_smart_fill_select(bpy.types.Operator):
    bl_idname = "fillmesh.smart_fill_select"
    bl_label = "Smart Fill Select"
    bl_description = "Grid-fill on selected boundary (robust: converts verts -> edges, fallback to non-manifold edges)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH'

    def execute(self, context):
        prev_mode = preserve_mode_context(context)
        try:
            obj = context.object
            if obj is None or obj.type != 'MESH':
                self.report({'ERROR'}, "Active object must be a mesh")
                return {'CANCELLED'}

            if context.mode != 'EDIT_MESH':
                bpy.ops.object.mode_set(mode='EDIT')

            try:
                bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type='EDGE')
            except Exception:
                try:
                    bpy.ops.mesh.select_mode(type='EDGE')
                except Exception:
                    pass

            try:
                bm = bmesh.from_edit_mesh(obj.data)
            except Exception:
                self.report({'ERROR'}, "Unable to access edit bmesh")
                return {'CANCELLED'}

            selected_edges = [e for e in bm.edges if e.select]

            if not selected_edges:
                sel_verts = [v for v in bm.verts if v.select]
                if sel_verts:
                    for v in sel_verts:
                        for e in v.link_edges:
                            e.select = True
                    bmesh.update_edit_mesh(obj.data)
                    selected_edges = [e for e in bm.edges if e.select]

            if not selected_edges:
                for e in bm.edges:
                    if not e.is_manifold:
                        e.select = True
                bmesh.update_edit_mesh(obj.data)
                selected_edges = [e for e in bm.edges if e.select]

            if not selected_edges:
                self.report({'WARNING'}, "No edges available for grid fill. Select an edge loop or boundary edges and retry.")
                return {'CANCELLED'}

            try:
                bpy.ops.mesh.fill_grid()
                self.report({'INFO'}, "Grid fill executed")
                return {'FINISHED'}
            except Exception as e:
                self.report({'ERROR'}, f"Grid fill failed: {e}")
                return {'CANCELLED'}
        finally:
            restore_mode_context(prev_mode)


class FILLMESH_OT_merge_vertex_group(bpy.types.Operator):
    bl_idname = "fillmesh.merge_vertex_group"
    bl_label = "Merge Vertex Group"
    bl_description = "Merge selected vertices within a threshold"
    bl_options = {"REGISTER", "UNDO"}
    threshold: bpy.props.FloatProperty(name="Threshold", default=0.015, min=0.0, max=1.0)

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH' and context.mode == 'EDIT_MESH'

    def execute(self, context):
        prefs = None
        try:
            prefs = context.preferences.addons.get(__package__).preferences if context.preferences.addons.get(__package__) else None
        except Exception:
            prefs = None
        threshold = getattr(prefs, "merge_threshold", self.threshold) if prefs else self.threshold
        obj = context.object
        bm = bmesh.from_edit_mesh(obj.data)
        selected_verts = [v for v in bm.verts if v.select]
        if not selected_verts:
            self.report({'WARNING'}, "No vertices selected")
            return {'CANCELLED'}
        res = None
        try:
            res = bmesh.ops.remove_doubles(bm, verts=selected_verts, dist=threshold)
        except Exception:
            res = None
        targetmap = res.get("targetmap", {}) if res else {}
        merged_count = len(targetmap) if targetmap else 0
        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Merged vertices: {merged_count}")
        return {'FINISHED'}


class FILLMESH_OT_snap_vertex(bpy.types.Operator):
    bl_idname = "fillmesh.snap_vertex"
    bl_label = "Snap Vertex (merge nearby)"
    bl_description = "Snap selected vertices together by averaging positions if close"
    bl_options = {"REGISTER", "UNDO"}
    threshold: bpy.props.FloatProperty(name="Threshold", default=0.05, min=0.0, max=1.0)

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH' and context.mode == 'EDIT_MESH'

    def execute(self, context):
        obj = context.object
        try:
            bm = bmesh.from_edit_mesh(obj.data)
        except Exception:
            self.report({'ERROR'}, "Unable to access edit bmesh")
            return {'CANCELLED'}
        selected_verts = [v for v in bm.verts if v.select]
        if not selected_verts:
            self.report({'WARNING'}, "No vertices selected")
            return {'CANCELLED'}
        merged_verts = set()
        for i, v in enumerate(selected_verts):
            for other in selected_verts[i+1:]:
                try:
                    if (v.co - other.co).length < self.threshold:
                        avg = (v.co + other.co) / 2.0
                        v.co = avg
                        other.co = avg
                        merged_verts.add(v)
                        merged_verts.add(other)
                except Exception:
                    pass
        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Vertices adjusted: {len(merged_verts)}")
        return {'FINISHED'}


class FILLMESH_OT_simple_bridge_fill(bpy.types.Operator):
    bl_idname = "fillmesh.simple_bridge_fill"
    bl_label = "Simple Bridge Fill"
    bl_description = "Bridge two selected edge loops"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH'

    def execute(self, context):
        prev_mode = preserve_mode_context(context)
        try:
            if context.mode != 'EDIT_MESH':
                bpy.ops.object.mode_set(mode='EDIT')
            try:
                bpy.ops.mesh.select_mode(type='EDGE')
            except Exception:
                pass
            try:
                bm = bmesh.from_edit_mesh(context.object.data)
            except Exception:
                self.report({'ERROR'}, "Unable to access edit bmesh")
                return {'CANCELLED'}
            selected_edges = [e for e in bm.edges if e.select]
            if len(selected_edges) < 2:
                self.report({'ERROR'}, "Select at least two edge loops (edges) to bridge")
                return {'CANCELLED'}
            try:
                bpy.ops.mesh.bridge_edge_loops()
                self.report({'INFO'}, "Bridge executed")
                return {'FINISHED'}
            except Exception as e:
                self.report({'ERROR'}, f"Bridge failed: {e}")
                return {'CANCELLED'}
        finally:
            restore_mode_context(prev_mode)


# ---------------------------------------------------------------------------
# Register / Unregister
# ---------------------------------------------------------------------------

classes = (
    FillMeshPreferences,
    FILLMESH_PT_main_panel,
    FILLMESH_OT_simple_quad_fill,
    FILLMESH_OT_fill_mesh,
    FILLMESH_OT_fill_shape,
    FILLMESH_OT_fill_mesh_select,
    FILLMESH_OT_detect_hole,
    FILLMESH_OT_fill_shape_select,
    FILLMESH_OT_smart_fill_select,
    FILLMESH_OT_snap_vertex,
    FILLMESH_OT_merge_vertex_group,
    FILLMESH_OT_simple_bridge_fill,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.fillmesh_section_fillbridge = bpy.props.BoolProperty(
        name="Fill & Bridge section expanded",
        default=True,
    )
    bpy.types.Scene.fillmesh_section_tools = bpy.props.BoolProperty(
        name="Fill Tools & Utilities section expanded",
        default=True,
    )
    print("Fill Mesh addon registered - open N-panel in 3D View and check 'Fill Mesh' tab")


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

    for prop in ("fillmesh_section_fillbridge", "fillmesh_section_tools"):
        try:
            delattr(bpy.types.Scene, prop)
        except Exception:
            pass

    print("Fill Mesh addon unregistered")


if __name__ == "__main__":
    register()