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

# NOTE: bl_info removed - blender_manifest.toml is the single source of
# metadata for Blender 4.2+ extensions.

import bpy
import bmesh
from typing import List
import math
from bpy.props import IntProperty

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

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


def smooth_vertices(bm: bmesh.types.BMesh, verts, factor: float = 1.0):
    if not verts:
        return
    try:
        bmesh.ops.smooth_vert(bm, verts=list(verts), factor=factor,
                              use_axis_x=True, use_axis_y=True, use_axis_z=True)
    except Exception:
        pass


def edit_mesh_poll(context) -> bool:
    """Common poll: active mesh object in Edit Mode."""
    obj = context.active_object
    return obj is not None and obj.type == 'MESH' and context.mode == 'EDIT_MESH'


# ---------------------------------------------------------------------------
# OpenQuad: Boundary Quad Propagation
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
    except ValueError:
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
        except ValueError:
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
        except ValueError:
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
        return edit_mesh_poll(context)

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
        description="Default distance threshold used by Merge by Distance / Snap Vertex"
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="Fill Mesh Addon Preferences")
        layout.prop(self, "merge_threshold")


def get_merge_threshold(context, fallback=0.015):
    addon = context.preferences.addons.get(__package__)
    if addon and hasattr(addon.preferences, "merge_threshold"):
        return addon.preferences.merge_threshold
    return fallback


# ---------------------------------------------------------------------------
# Helper to provide dynamic EnumProperty items for holes
# ---------------------------------------------------------------------------

def get_holes_enum(self, context):
    items = []
    obj = context.active_object
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        return items
    try:
        bm = bmesh.from_edit_mesh(obj.data)
        boundary_edges = [e for e in bm.edges if not e.is_manifold]
        if not boundary_edges:
            return items
        loops = loops_from_boundary_edges(boundary_edges)
        items.append(('ALL', "All holes", "Select all detected holes"))
        for i, loop in enumerate(loops):
            items.append((str(i), f"Hole {i+1} ({len(loop)} edges)", f"Boundary loop with {len(loop)} edges"))
    except Exception:
        items = []
    return items


# ---------------------------------------------------------------------------
# UI Panel (Mesh Edit mode only)
# ---------------------------------------------------------------------------

class FILLMESH_PT_main_panel(bpy.types.Panel):
    bl_label = "Fill Mesh"
    bl_idname = "FILLMESH_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Fill Mesh"
    bl_context = "mesh_edit"

    @classmethod
    def poll(cls, context):
        return edit_mesh_poll(context)

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Fill & Bridge section
        row = layout.row(align=True)
        icon = 'TRIA_DOWN' if getattr(scene, "fillmesh_section_fillbridge", True) else 'TRIA_RIGHT'
        row.prop(scene, "fillmesh_section_fillbridge", icon=icon, emboss=False, text="Fill & Bridge")
        if scene.fillmesh_section_fillbridge:
            sub = layout.row(align=True)
            sub.operator_context = "INVOKE_DEFAULT"
            sub.operator("fillmesh.repair_notches", text="Repair Notches")
            sub.operator("mesh.fill_grid", text="Grid Fill")
            layout.separator()
            layout.operator("fillmesh.detect_hole", text="Detect Hole")

        # Fill Tools & Utilities
        row = layout.row(align=True)
        icon = 'TRIA_DOWN' if getattr(scene, "fillmesh_section_tools", True) else 'TRIA_RIGHT'
        row.prop(scene, "fillmesh_section_tools", icon=icon, emboss=False, text="Fill Tools & Utilities")
        if scene.fillmesh_section_tools:
            grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=True)
            grid.operator("fillmesh.snap_vertex", text="Snap Vertex")
            # Native Merge by Distance, pre-filled with the addon preference
            op = grid.operator("mesh.remove_doubles", text="Merge by Distance")
            op.threshold = get_merge_threshold(context)
            grid.operator("fillmesh.fill_mesh", text="Fill Mesh")
            grid.operator("fillmesh.fill_mesh_select", text="Fill Mesh Select")
            grid.operator("fillmesh.fill_shape", text="Fill Shape")
            grid.operator("fillmesh.fill_shape_select", text="Fill Shape Select")
            layout.separator()
            layout.operator("fillmesh.simple_quad_fill", text="Simple Quad Fill", icon='MESH_PLANE')


# ---------------------------------------------------------------------------
# Detect Hole operator
# ---------------------------------------------------------------------------

class FILLMESH_OT_detect_hole(bpy.types.Operator):
    bl_idname = "fillmesh.detect_hole"
    bl_label = "Detect Hole"
    bl_description = "Detect and select holes on the active mesh (choose hole in Adjust Last Operation)"
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
        return edit_mesh_poll(context)

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        bm = bmesh.from_edit_mesh(mesh)

        bm.select_mode |= {'VERT'}
        bm.select_flush(True)

        boundary_edges = [e for e in bm.edges if not e.is_manifold]
        if not boundary_edges:
            self.report({'INFO'}, "No holes detected")
            return {'FINISHED'}

        boundary_loops = loops_from_boundary_edges(boundary_edges)

        chosen = self.hole

        # Sort holes by size (largest first)
        boundary_loops.sort(key=lambda loop: len(loop), reverse=True)

        # Slider modes
        if self.radius > 0:
            if self.single_hole_mode:
                idx = min(max(self.radius - 1, 0), max(len(boundary_loops) - 1, 0))
                if boundary_loops:
                    boundary_loops = [boundary_loops[idx]]
            else:
                boundary_loops = boundary_loops[:min(self.radius, len(boundary_loops))]

        # Clear previous selection (verts + edges)
        for v in bm.verts:
            v.select = False
        for e in bm.edges:
            e.select = False

        if chosen in (None, "", "ALL"):
            count = 0
            for loop in boundary_loops:
                for edge in loop:
                    edge.select = True
                    edge.verts[0].select = True
                    edge.verts[1].select = True
                count += 1

            bmesh.update_edit_mesh(mesh)
            self.report({'INFO'}, f"Detected and selected {count} hole(s)")
            return {'FINISHED'}

        # Specific hole chosen
        try:
            idx = int(chosen)
        except ValueError:
            self.report({'ERROR'}, "Invalid hole selection")
            return {'CANCELLED'}

        if idx < 0 or idx >= len(boundary_loops):
            self.report({'ERROR'}, "Selected hole index out of range")
            return {'CANCELLED'}

        chosen_loop = boundary_loops[idx]
        for edge in chosen_loop:
            edge.select = True
            edge.verts[0].select = True
            edge.verts[1].select = True

        bmesh.update_edit_mesh(mesh)
        self.report({'INFO'}, f"Detected and selected Hole {idx+1}")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Fill operators (Edit Mode only)
# ---------------------------------------------------------------------------

class FILLMESH_OT_fill_mesh(bpy.types.Operator):
    bl_idname = "fillmesh.fill_mesh"
    bl_label = "Fill Mesh"
    bl_description = "Fix and fill all holes on the mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return edit_mesh_poll(context)

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        boundary_edges = find_boundary_edges(bm)
        if not boundary_edges:
            self.report({'INFO'}, "No boundary edges (holes) found")
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

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Holes processed: {count}")
        return {'FINISHED'}


class FILLMESH_OT_fill_shape(bpy.types.Operator):
    bl_idname = "fillmesh.fill_shape"
    bl_label = "Fill Shape"
    bl_description = "Calculate shape, fix and fill all holes"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return edit_mesh_poll(context)

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        boundary_edges = find_boundary_edges(bm)
        if not boundary_edges:
            self.report({'INFO'}, "No boundary edges found")
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

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, "Fill Shape: Completed")
        return {'FINISHED'}


class FILLMESH_OT_fill_mesh_select(bpy.types.Operator):
    bl_idname = "fillmesh.fill_mesh_select"
    bl_label = "Fill Mesh Select"
    bl_description = "Fix and fill hole using selected boundary"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return edit_mesh_poll(context)

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

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


class FILLMESH_OT_fill_shape_select(bpy.types.Operator):
    bl_idname = "fillmesh.fill_shape_select"
    bl_label = "Fill Shape Select"
    bl_description = "Fill selection with computed shape"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return edit_mesh_poll(context)

    def execute(self, context):
        obj = context.active_object
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


# ---------------------------------------------------------------------------
# Vertex utilities (custom logic, not wrappers)
# ---------------------------------------------------------------------------

class FILLMESH_OT_snap_vertex(bpy.types.Operator):
    bl_idname = "fillmesh.snap_vertex"
    bl_label = "Snap Vertex (average nearby)"
    bl_description = "Snap selected vertices together by averaging positions if close (without merging)"
    bl_options = {"REGISTER", "UNDO"}

    threshold: bpy.props.FloatProperty(name="Threshold", default=0.05, min=0.0, max=1.0)

    @classmethod
    def poll(cls, context):
        return edit_mesh_poll(context)

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        selected_verts = [v for v in bm.verts if v.select]
        if not selected_verts:
            self.report({'WARNING'}, "No vertices selected")
            return {'CANCELLED'}

        adjusted = set()
        for i, v in enumerate(selected_verts):
            for other in selected_verts[i + 1:]:
                if (v.co - other.co).length < self.threshold:
                    avg = (v.co + other.co) / 2.0
                    v.co = avg
                    other.co = avg
                    adjusted.add(v)
                    adjusted.add(other)

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Vertices adjusted: {len(adjusted)}")
        return {'FINISHED'}



# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Repair Notches helpers
# ---------------------------------------------------------------------------

def quad_already_exists(verts):
    """True if a single face already uses exactly these vertices."""
    shared = set(verts[0].link_faces)
    for vert in verts[1:]:
        shared &= set(vert.link_faces)
    return bool(shared)


def repair_all_notches(bm, tol_factor=0.5, max_passes=64):
    """Fill missing quads around the selected hole boundary.

    Feeds each boundary edge one at a time to propagate_quad_from_boundary
    (exactly like the user clicking Simple Quad Fill on each edge manually).
    Iterates until no more quads can be created."""
    all_created = []

    # Find the target hole
    boundary_edges = find_boundary_edges(bm)
    if not boundary_edges:
        return []

    loops = loops_from_boundary_edges(boundary_edges)
    target_loop = next((lp for lp in loops if any(e.select for e in lp)), None)
    if target_loop is None:
        if len(loops) == 1:
            target_loop = loops[0]
        else:
            return []

    for _pass in range(max_passes):
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        # Refresh boundary — it changes as we add faces
        current_boundary = find_boundary_edges(bm)
        if not current_boundary:
            break

        created_this_pass = []

        for seed_edge in list(current_boundary):
            if not seed_edge.is_valid:
                continue
            # Select only this one edge so propagate_quad_from_boundary works
            for e in bm.edges:
                e.select = False
            seed_edge.select = True

            face = propagate_quad_from_boundary(bm)
            if face is not None:
                created_this_pass.append(face)
                all_created.append(face)
                # Re-sync after topology change
                bm.edges.ensure_lookup_table()
                bm.verts.ensure_lookup_table()
                bm.faces.ensure_lookup_table()

        if not created_this_pass:
            break

    return all_created


def _hole_loop_for(bm, prefer_selection=True):
    """Return the boundary loop to act on: the one touching the current
    selection, or the only hole if there is exactly one. Returns (loop, error)."""
    boundary_edges = find_boundary_edges(bm)
    if not boundary_edges:
        return None, "No holes found"
    loops = loops_from_boundary_edges(boundary_edges)
    if prefer_selection:
        sel = next((lp for lp in loops if any(e.select for e in lp)), None)
        if sel is not None:
            return sel, None
    if len(loops) == 1:
        return loops[0], None
    return None, "Multiple holes found - select an edge of the hole to target"


# Smart Fill (2-step workflow)
# ---------------------------------------------------------------------------

class FILLMESH_OT_repair_notches(bpy.types.Operator):
    bl_idname = "fillmesh.repair_notches"
    bl_label = "Repair Notches"
    bl_description = ("Step 1: fill the missing quads of an irregular hole so the "
                      "boundary becomes regular. Then use Grid Fill Hole")
    bl_options = {"REGISTER", "UNDO"}

    max_passes: bpy.props.IntProperty(
        name="Max Passes",
        description="Safety limit on repair iterations",
        default=64, min=1, max=1024,
    )

    @classmethod
    def poll(cls, context):
        return edit_mesh_poll(context)

    def execute(self, context):
        obj = context.active_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)

        created = repair_all_notches(bm, max_passes=self.max_passes)

        for v in bm.verts:
            v.select = False
        for e in bm.edges:
            e.select = False
        for f in bm.faces:
            f.select = False
        for f in created:
            if f.is_valid:
                f.select = True
                for e in f.edges:
                    e.select = True
                for v in f.verts:
                    v.select = True
        bmesh.update_edit_mesh(me)

        if created:
            self.report({'INFO'}, f"Repaired {len(created)} quad(s). Now run Grid Fill Hole")
        else:
            self.report({'INFO'}, "No notches found - boundary already regular. Run Grid Fill Hole")
        return {'FINISHED'}



# ---------------------------------------------------------------------------
# Register / Unregister
# ---------------------------------------------------------------------------

classes = (
    FillMeshPreferences,
    FILLMESH_PT_main_panel,
    FILLMESH_OT_simple_quad_fill,
    FILLMESH_OT_repair_notches,
    FILLMESH_OT_fill_mesh,
    FILLMESH_OT_fill_shape,
    FILLMESH_OT_fill_mesh_select,
    FILLMESH_OT_detect_hole,
    FILLMESH_OT_fill_shape_select,
    FILLMESH_OT_snap_vertex,
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



def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


    for prop in ("fillmesh_section_fillbridge", "fillmesh_section_tools"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)


if __name__ == "__main__":
    register()
