"""Phase 10: 3D Building Information Modeling (BIM) geometry generator and Wavefront OBJ exporter.

Extrudes 2D metric floor plans into full 3D architectural models:
- Triangulated floor slabs at z = 0.0m with pastel zone fills.
- 2.70m vertical walls in dark charcoal (#2B2B2B).
- Doorway lintels (z = 2.10m to 2.70m) leaving 2.10m walk-through clearance.
- Semi-translucent glass windows (sill 0.90m to 2.10m, #A0C4DF).
- Ceiling sensor device pods (green/white).
- Wall-mounted telecom hub equipment cabinets (z = 0.80m to 1.60m, center 1.20m).
- Elevated orthogonal cable conduits at z = 2.55m with vertical drops to hubs and nodes.
- Wavefront .obj architectural export for Autodesk Revit, Blender, and AutoCAD.
"""

import math
from typing import Any, Dict, List, Optional, Tuple, Union
import shapely
import shapely.geometry.polygon
import shapely.ops
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon

from resplan_router.contracts import (
    BIM3DScene,
    BIMElement3D,
    BIMElementType,
    DoorOrPassagePortal,
    HubAssignment,
    HubZonePartition,
    MetricFloorPlan,
    SynthesizedRingsBundle,
)
from resplan_router.phase1_ingestion.parser import parse_and_heal_polygon
from resplan_router.phase1_ingestion.scale_converter import normalize_to_metric, parse_geometry
from resplan_router.phase2_topology.portal_extractor import extract_all_portals

# Standard architectural and routing color palette
WALL_COLOR = "#2B2B2B"
DOOR_LINTEL_COLOR = "#3C3C3C"
WINDOW_COLOR = "#A0C4DF"
NODE_POD_COLOR = "#00E676"
HUB_CABINET_COLOR = "#1565C0"
EXCLUDED_SLAB_COLOR = "#EEEEEE"

ZONE_PASTEL_COLORS = [
    "#DCE8F8",  # Soft pastel blue (Hub 1)
    "#FDE5D2",  # Soft pastel peach (Hub 2)
    "#E8F5E9",  # Soft pastel mint (Hub 3)
    "#F3E5F5",  # Soft pastel lavender (Hub 4)
    "#FFF9C4",  # Soft pastel lemon (Hub 5)
]

RING_HIGH_CONTRAST_COLORS = [
    "#00D2FF",  # Vibrant cyan (Ring 1)
    "#FFA500",  # Vibrant amber/orange (Ring 2)
    "#7C4DFF",  # Deep purple (Ring 3)
    "#00E676",  # Neon green (Ring 4)
    "#FF5252",  # Coral red (Ring 5)
]


def triangulate_polygon_2d(poly: Polygon) -> Tuple[List[List[float]], List[List[int]]]:
    """Triangulate a 2D shapely Polygon into deduplicated 2D vertices and triangle faces.

    Args:
        poly: Input 2D polygon.

    Returns:
        Tuple of (vertices_2d [ [x, y], ... ], triangle_faces [ [i, j, k], ... ]).
    """
    if poly.is_empty or poly.area < 1e-6:
        return [], []

    tris = shapely.ops.triangulate(poly)
    valid_tris = [t for t in tris if poly.buffer(1e-5).contains(t.representative_point())]

    verts_2d: List[List[float]] = []
    vert_map: Dict[Tuple[float, float], int] = {}
    faces: List[List[int]] = []

    for t in valid_tris:
        coords = list(t.exterior.coords)[:-1]
        if len(coords) < 3:
            continue
        face: List[int] = []
        for x, y in coords:
            key = (round(float(x), 4), round(float(y), 4))
            if key not in vert_map:
                vert_map[key] = len(verts_2d)
                verts_2d.append([key[0], key[1]])
            face.append(vert_map[key])
        faces.append(face)

    return verts_2d, faces


def extrude_polygon_3d(
    poly: Polygon,
    z_min: float,
    z_max: float,
) -> Tuple[List[List[float]], List[List[int]]]:
    """Extrude a 2D polygon vertically into a closed 3D prism.

    Args:
        poly: 2D base polygon.
        z_min: Bottom elevation in metres.
        z_max: Top elevation in metres.

    Returns:
        Tuple of (vertices_3d [ [x, y, z], ... ], faces [ [i, j, k], ... ]).
    """
    if poly.is_empty or poly.area < 1e-6:
        return [], []

    poly = shapely.geometry.polygon.orient(poly, sign=1.0)
    verts_2d, tri_faces_2d = triangulate_polygon_2d(poly)
    if not verts_2d:
        return [], []

    m = len(verts_2d)
    verts_3d: List[List[float]] = (
        [[round(x, 4), round(y, 4), round(z_min, 4)] for x, y in verts_2d]
        + [[round(x, 4), round(y, 4), round(z_max, 4)] for x, y in verts_2d]
    )

    faces: List[List[int]] = []

    # Bottom cap (reversed winding for outward -z normal)
    for a, b, c in tri_faces_2d:
        faces.append([a, c, b])

    # Top cap (+z normal)
    for a, b, c in tri_faces_2d:
        faces.append([a + m, b + m, c + m])

    # Side walls
    vert_map = {(verts_2d[i][0], verts_2d[i][1]): i for i in range(m)}
    rings = [poly.exterior] + list(poly.interiors)
    for ring in rings:
        coords = list(ring.coords)[:-1]
        n_coords = len(coords)
        for i in range(n_coords):
            p1 = (round(coords[i][0], 4), round(coords[i][1], 4))
            p2 = (round(coords[(i + 1) % n_coords][0], 4), round(coords[(i + 1) % n_coords][1], 4))
            if p1 in vert_map and p2 in vert_map:
                idx1 = vert_map[p1]
                idx2 = vert_map[p2]
                faces.append([idx1, idx2, idx2 + m])
                faces.append([idx1, idx2 + m, idx1 + m])

    return verts_3d, faces


def create_box_3d(
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    zmin: float,
    zmax: float,
) -> Tuple[List[List[float]], List[List[int]]]:
    """Construct an axis-aligned 3D box with 8 vertices and 12 outward triangular faces.

    Args:
        xmin, xmax: X bounds in metres.
        ymin, ymax: Y bounds in metres.
        zmin, zmax: Z bounds in metres.

    Returns:
        Tuple of (vertices_3d, faces).
    """
    xmin, xmax = min(xmin, xmax), max(xmin, xmax)
    ymin, ymax = min(ymin, ymax), max(ymin, ymax)
    zmin, zmax = min(zmin, zmax), max(zmin, zmax)

    verts = [
        [round(xmin, 4), round(ymin, 4), round(zmin, 4)],  # 0
        [round(xmax, 4), round(ymin, 4), round(zmin, 4)],  # 1
        [round(xmax, 4), round(ymax, 4), round(zmin, 4)],  # 2
        [round(xmin, 4), round(ymax, 4), round(zmin, 4)],  # 3
        [round(xmin, 4), round(ymin, 4), round(zmax, 4)],  # 4
        [round(xmax, 4), round(ymin, 4), round(zmax, 4)],  # 5
        [round(xmax, 4), round(ymax, 4), round(zmax, 4)],  # 6
        [round(xmin, 4), round(ymax, 4), round(zmax, 4)],  # 7
    ]
    faces = [
        # bottom (-z)
        [0, 2, 1],
        [0, 3, 2],
        # top (+z)
        [4, 5, 6],
        [4, 6, 7],
        # front (-y)
        [0, 1, 5],
        [0, 5, 4],
        # back (+y)
        [2, 3, 7],
        [2, 7, 6],
        # left (-x)
        [3, 0, 4],
        [3, 4, 7],
        # right (+x)
        [1, 2, 6],
        [1, 6, 5],
    ]
    return verts, faces


def compute_vertex_normals(
    verts: List[List[float]],
    faces: List[List[int]],
) -> List[List[float]]:
    """Compute per-vertex outward unit normals from face geometry."""
    normals = [[0.0, 0.0, 0.0] for _ in verts]
    for face in faces:
        if len(face) < 3:
            continue
        v0, v1, v2 = verts[face[0]], verts[face[1]], verts[face[2]]
        u = [v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]]
        v = [v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]]
        nx = u[1] * v[2] - u[2] * v[1]
        ny = u[2] * v[0] - u[0] * v[2]
        nz = u[0] * v[1] - u[1] * v[0]
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 1e-9:
            nx /= length
            ny /= length
            nz /= length
            for idx in face:
                normals[idx][0] += nx
                normals[idx][1] += ny
                normals[idx][2] += nz

    unit_normals: List[List[float]] = []
    for nx, ny, nz in normals:
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 1e-9:
            unit_normals.append([round(nx / length, 4), round(ny / length, 4), round(nz / length, 4)])
        else:
            unit_normals.append([0.0, 0.0, 1.0])
    return unit_normals


class BIM3DGenerator:
    """Parametric 3D Building Information Modeling (BIM) generator for architectural floor plans."""

    def __init__(self) -> None:
        pass

    def generate_3d_scene(
        self,
        plan: MetricFloorPlan,
        partition: Optional[HubZonePartition] = None,
        bundle: Optional[SynthesizedRingsBundle] = None,
        ceiling_height_m: float = 2.70,
        conduit_elevation_m: float = 2.55,
        raw_json: Optional[Dict[str, Any]] = None,
        portals: Optional[List[DoorOrPassagePortal]] = None,
        walls: Optional[List[Polygon]] = None,
        windows: Optional[List[Polygon]] = None,
    ) -> BIM3DScene:
        """Extrude 2D floor plan and routing bundle into a complete 3D BIM scene.

        Args:
            plan: Normalized MetricFloorPlan.
            partition: Optional HubZonePartition with assigned hubs and zones.
            bundle: Optional SynthesizedRingsBundle with closed cable routes.
            ceiling_height_m: Total ceiling height in metres (default 2.70m).
            conduit_elevation_m: Elevation for elevated orthogonal cable conduits (default 2.55m).
            raw_json: Optional original JSON dictionary for explicit wall/window/door coordinates.
            portals: Optional pre-extracted list of DoorOrPassagePortal objects.
            walls: Optional explicit list of wall polygons in metres.
            windows: Optional explicit list of window polygons in metres.

        Returns:
            BIM3DScene: Complete 3D architectural and electrical mesh scene.
        """
        elements: List[BIMElement3D] = []

        # 1. Map room_id to assigned hub index for zone colors
        room_to_hub_idx: Dict[str, int] = {}
        room_to_hub_id: Dict[str, str] = {}
        if partition is not None:
            for idx, hub in enumerate(partition.hubs):
                for r_id in hub.assigned_rooms:
                    room_to_hub_idx[r_id] = idx
                    room_to_hub_id[r_id] = hub.hub_id
        elif bundle is not None:
            for idx, ring in enumerate(bundle.hub_rings):
                for r_id in ring.assigned_rooms:
                    room_to_hub_idx[r_id] = idx
                    room_to_hub_id[r_id] = ring.hub_id

        # -------------------------------------------------------------------------
        # A. Room Floor Slabs at z = 0.0m
        # -------------------------------------------------------------------------
        for r_id, room in plan.rooms.items():
            poly = shapely.from_wkt(room.polygon_wkt)
            if poly.is_empty:
                continue

            verts_2d, tri_faces = triangulate_polygon_2d(poly)
            if not verts_2d or not tri_faces:
                continue

            verts_3d = [[round(x, 4), round(y, 4), 0.0] for x, y in verts_2d]

            hub_idx = room_to_hub_idx.get(r_id)
            if hub_idx is not None:
                color_hex = ZONE_PASTEL_COLORS[hub_idx % len(ZONE_PASTEL_COLORS)]
                assigned_hub = room_to_hub_id.get(r_id)
            else:
                color_hex = EXCLUDED_SLAB_COLOR
                assigned_hub = None

            elements.append(
                BIMElement3D(
                    element_id=f"slab_{r_id}",
                    element_type=BIMElementType.SLAB,
                    room_id=r_id,
                    hub_id=assigned_hub,
                    vertices_3d=verts_3d,
                    faces=tri_faces,
                    color_hex=color_hex,
                    opacity=1.0,
                    metadata={
                        "room_id": r_id,
                        "category": room.category,
                        "area_m2": room.area_m2,
                        "elevation_z": 0.0,
                        "assigned_hub": assigned_hub,
                    },
                )
            )

        # -------------------------------------------------------------------------
        # B. Architectural Walls from z = 0.0m to z = ceiling_height_m (2.70m)
        # -------------------------------------------------------------------------
        wall_polygons: List[Polygon] = []
        if walls is not None and len(walls) > 0:
            wall_polygons.extend(walls)
        elif raw_json is not None and "walls" in raw_json and isinstance(raw_json["walls"], list):
            scale = plan.scale_units_per_m
            raw_inner = parse_geometry(raw_json["inner"])
            min_x, min_y, _, _ = raw_inner.bounds
            origin_offset = (min_x, min_y)
            for raw_w in raw_json["walls"]:
                if raw_w:
                    scaled_w = normalize_to_metric(parse_geometry(raw_w), scale, origin_offset)
                    healed_w = parse_and_heal_polygon(scaled_w, min_area=0.0, is_structural_feature=True)
                    if isinstance(healed_w, Polygon) and not healed_w.is_empty:
                        wall_polygons.append(healed_w)
                    elif isinstance(healed_w, MultiPolygon):
                        wall_polygons.extend([p for p in healed_w.geoms if not p.is_empty])
        else:
            obs = plan.obstacles_geom
            if isinstance(obs, Polygon) and not obs.is_empty:
                wall_polygons.append(obs)
            elif isinstance(obs, MultiPolygon):
                wall_polygons.extend([p for p in obs.geoms if not p.is_empty])

        for w_idx, w_poly in enumerate(wall_polygons):
            verts_3d, faces = extrude_polygon_3d(w_poly, 0.0, ceiling_height_m)
            if verts_3d and faces:
                elements.append(
                    BIMElement3D(
                        element_id=f"wall_{w_idx:02d}",
                        element_type=BIMElementType.WALL,
                        vertices_3d=verts_3d,
                        faces=faces,
                        color_hex=WALL_COLOR,
                        opacity=1.0,
                        metadata={
                            "wall_index": w_idx,
                            "base_z": 0.0,
                            "top_z": ceiling_height_m,
                            "height_m": ceiling_height_m,
                        },
                    )
                )

        # -------------------------------------------------------------------------
        # C. Doorway Portals (Door Lintels from z = 2.10m to z = 2.70m)
        # -------------------------------------------------------------------------
        portal_list: List[DoorOrPassagePortal] = []
        if portals is not None:
            portal_list = portals
        elif raw_json is not None and ("doors" in raw_json or "front_door" in raw_json):
            portal_list = extract_all_portals(
                plan,
                raw_doors=raw_json.get("doors"),
                raw_entrance=raw_json.get("front_door"),
            )
        else:
            portal_list = extract_all_portals(plan)

        for portal in portal_list:
            footprint = portal.threshold_geom.buffer(0.10, cap_style="flat")
            if isinstance(footprint, MultiPolygon):
                lintel_polys = [p for p in footprint.geoms if not p.is_empty]
            elif isinstance(footprint, Polygon) and not footprint.is_empty:
                lintel_polys = [footprint]
            else:
                lintel_polys = []

            for l_idx, l_poly in enumerate(lintel_polys):
                verts_3d, faces = extrude_polygon_3d(l_poly, 2.10, ceiling_height_m)
                if verts_3d and faces:
                    elements.append(
                        BIMElement3D(
                            element_id=f"door_lintel_{portal.portal_id}_{l_idx}",
                            element_type=BIMElementType.DOOR_LINTEL,
                            room_id=portal.room1_id,
                            vertices_3d=verts_3d,
                            faces=faces,
                            color_hex=DOOR_LINTEL_COLOR,
                            opacity=1.0,
                            metadata={
                                "portal_id": portal.portal_id,
                                "sill_z": 2.10,
                                "top_z": ceiling_height_m,
                                "clearance_m": 2.10,
                                "width_m": portal.width_m,
                                "room1": portal.room1_id,
                                "room2": portal.room2_id,
                            },
                        )
                    )

        # -------------------------------------------------------------------------
        # D. Windows (Extruded from sill height 0.90m to 2.10m, translucent)
        # -------------------------------------------------------------------------
        win_polygons: List[Polygon] = []
        if windows is not None and len(windows) > 0:
            win_polygons.extend(windows)
        elif raw_json is not None and "windows" in raw_json and isinstance(raw_json["windows"], list) and len(raw_json["windows"]) > 0:
            scale = plan.scale_units_per_m
            raw_inner = parse_geometry(raw_json["inner"])
            min_x, min_y, _, _ = raw_inner.bounds
            origin_offset = (min_x, min_y)
            for raw_win in raw_json["windows"]:
                if raw_win:
                    scaled_win = normalize_to_metric(parse_geometry(raw_win), scale, origin_offset)
                    healed_win = parse_and_heal_polygon(scaled_win, min_area=0.0, is_structural_feature=True)
                    if isinstance(healed_win, Polygon) and not healed_win.is_empty:
                        win_polygons.append(healed_win)
                    elif isinstance(healed_win, MultiPolygon):
                        win_polygons.extend([p for p in healed_win.geoms if not p.is_empty])
        else:
            # Synthesize architectural windows along exterior perimeter walls of eligible rooms
            inner_ext = plan.inner_geom.exterior
            win_idx = 0
            for r_id, room in plan.rooms.items():
                if not room.is_eligible:
                    continue
                r_geom = shapely.from_wkt(room.polygon_wkt)
                shared = r_geom.intersection(inner_ext)
                if shared.is_empty:
                    continue
                shared_lines: List[LineString] = []
                if isinstance(shared, LineString):
                    shared_lines = [shared]
                elif isinstance(shared, MultiLineString):
                    shared_lines = [g for g in shared.geoms if isinstance(g, LineString)]

                for line in shared_lines:
                    if line.length >= 2.0:
                        target_len = min(2.0, line.length * 0.6)
                        start_dist = (line.length - target_len) / 2.0
                        end_dist = start_dist + target_len
                        p_start = line.interpolate(start_dist)
                        p_end = line.interpolate(end_dist)
                        sub_seg = LineString([p_start, p_end])
                        w_footprint = sub_seg.buffer(0.08, cap_style="flat")
                        if isinstance(w_footprint, Polygon) and not w_footprint.is_empty:
                            win_polygons.append(w_footprint)
                            win_idx += 1

        for w_idx, win_poly in enumerate(win_polygons):
            verts_3d, faces = extrude_polygon_3d(win_poly, 0.90, 2.10)
            if verts_3d and faces:
                elements.append(
                    BIMElement3D(
                        element_id=f"window_{w_idx:02d}",
                        element_type=BIMElementType.WINDOW,
                        vertices_3d=verts_3d,
                        faces=faces,
                        color_hex=WINDOW_COLOR,
                        opacity=0.5,
                        metadata={
                            "window_index": w_idx,
                            "sill_m": 0.90,
                            "top_m": 2.10,
                            "height_m": 1.20,
                        },
                    )
                )

        # -------------------------------------------------------------------------
        # E. Sensor Devices (Nodes at ceiling height)
        # -------------------------------------------------------------------------
        for node_id, node in plan.nodes.items():
            nx, ny = node.location.x, node.location.y
            # 3D pod: small cube/disc pod centered at (nx, ny), spanning z = 2.40m to 2.55m
            verts_3d, faces = create_box_3d(
                nx - 0.10,
                nx + 0.10,
                ny - 0.10,
                ny + 0.10,
                2.40,
                conduit_elevation_m,
            )
            elements.append(
                BIMElement3D(
                    element_id=f"node_{node_id}",
                    element_type=BIMElementType.NODE,
                    room_id=node.room_id,
                    vertices_3d=verts_3d,
                    faces=faces,
                    color_hex=NODE_POD_COLOR,
                    opacity=1.0,
                    metadata={
                        "node_id": node_id,
                        "room_id": node.room_id,
                        "x": nx,
                        "y": ny,
                        "elevation_z": conduit_elevation_m,
                    },
                )
            )

        # -------------------------------------------------------------------------
        # F. Hub Equipment Cabinets (Wall mounted at z = 0.80m to 1.60m, center 1.20m)
        # -------------------------------------------------------------------------
        hub_list: List[HubAssignment] = []
        if partition is not None:
            hub_list = partition.hubs
        elif bundle is not None:
            for ring in bundle.hub_rings:
                hub_list.append(
                    HubAssignment(
                        hub_id=ring.hub_id,
                        room_id=ring.room_id,
                        location=ring.location,
                        assigned_rooms=ring.assigned_rooms,
                        assigned_nodes=ring.assigned_nodes,
                        zone_area_m2=0.0,
                    )
                )

        for hub in hub_list:
            hx, hy = hub.location.x, hub.location.y
            # Cabinet dimensions: 0.36m width (X), 0.24m depth (Y), 0.80m height (Z) centered at 1.20m
            verts_3d, faces = create_box_3d(
                hx - 0.18,
                hx + 0.18,
                hy - 0.12,
                hy + 0.12,
                0.80,
                1.60,
            )
            elements.append(
                BIMElement3D(
                    element_id=f"hub_{hub.hub_id}",
                    element_type=BIMElementType.HUB,
                    room_id=hub.room_id,
                    hub_id=hub.hub_id,
                    vertices_3d=verts_3d,
                    faces=faces,
                    color_hex=HUB_CABINET_COLOR,
                    opacity=1.0,
                    metadata={
                        "hub_id": hub.hub_id,
                        "room_id": hub.room_id,
                        "x": hx,
                        "y": hy,
                        "center_z": 1.20,
                        "height_m": 0.80,
                    },
                )
            )

        # -------------------------------------------------------------------------
        # G. 3D Cable Conduits (Horizontal runs at 2.55m + Vertical drops)
        # -------------------------------------------------------------------------
        if bundle is not None:
            r_conduit = 0.02  # 20mm half-width duct

            for ring_idx, ring in enumerate(bundle.hub_rings):
                ring_color = RING_HIGH_CONTRAST_COLORS[ring_idx % len(RING_HIGH_CONTRAST_COLORS)]
                conduit_verts: List[List[float]] = []
                conduit_faces: List[List[int]] = []

                def add_conduit_box(xmin: float, xmax: float, ymin: float, ymax: float, zmin: float, zmax: float) -> None:
                    b_verts, b_faces = create_box_3d(xmin, xmax, ymin, ymax, zmin, zmax)
                    offset = len(conduit_verts)
                    conduit_verts.extend(b_verts)
                    for f in b_faces:
                        conduit_faces.append([idx + offset for idx in f])

                horizontal_segments_count = 0
                # 1. Horizontal conduit runs along route segments at z = conduit_elevation_m
                for seg in ring.route_segments:
                    pts = getattr(seg, "polyline", getattr(seg, "points", []))
                    for i in range(len(pts) - 1):
                        p1, p2 = pts[i], pts[i + 1]
                        dx = abs(p2.x - p1.x)
                        dy = abs(p2.y - p1.y)
                        if dx < 1e-4 and dy < 1e-4:
                            continue
                        if dx >= dy:
                            # Runs along X
                            add_conduit_box(
                                min(p1.x, p2.x),
                                max(p1.x, p2.x),
                                p1.y - r_conduit,
                                p1.y + r_conduit,
                                conduit_elevation_m - r_conduit,
                                conduit_elevation_m + r_conduit,
                            )
                        else:
                            # Runs along Y
                            add_conduit_box(
                                p1.x - r_conduit,
                                p1.x + r_conduit,
                                min(p1.y, p2.y),
                                max(p1.y, p2.y),
                                conduit_elevation_m - r_conduit,
                                conduit_elevation_m + r_conduit,
                            )
                        horizontal_segments_count += 1

                # 2. Vertical drop from ceiling conduit (2.55m) down to hub equipment cabinet (1.50m)
                hx, hy = ring.location.x, ring.location.y
                add_conduit_box(
                    hx - r_conduit,
                    hx + r_conduit,
                    hy - r_conduit,
                    hy + r_conduit,
                    1.50,
                    conduit_elevation_m,
                )
                hub_drop_info = {
                    "hub_id": ring.hub_id,
                    "top": [hx, hy, conduit_elevation_m],
                    "bottom": [hx, hy, 1.50],
                }

                # 3. Vertical drops from ceiling conduit (2.55m) down to device nodes (2.40m)
                node_drops_info = []
                for node_id in ring.assigned_nodes:
                    if node_id in plan.nodes:
                        n_pt = plan.nodes[node_id].location
                        add_conduit_box(
                            n_pt.x - r_conduit,
                            n_pt.x + r_conduit,
                            n_pt.y - r_conduit,
                            n_pt.y + r_conduit,
                            2.40,
                            conduit_elevation_m,
                        )
                        node_drops_info.append({
                            "node_id": node_id,
                            "top": [n_pt.x, n_pt.y, conduit_elevation_m],
                            "bottom": [n_pt.x, n_pt.y, 2.40],
                        })

                elements.append(
                    BIMElement3D(
                        element_id=f"conduit_{ring.hub_id}",
                        element_type=BIMElementType.CONDUIT,
                        hub_id=ring.hub_id,
                        vertices_3d=conduit_verts,
                        faces=conduit_faces,
                        color_hex=ring_color,
                        opacity=1.0,
                        metadata={
                            "hub_id": ring.hub_id,
                            "runs_at_elevation": conduit_elevation_m,
                            "has_vertical_drops": True,
                            "horizontal_segments": horizontal_segments_count,
                            "hub_drop": hub_drop_info,
                            "node_drops": node_drops_info,
                        },
                    )
                )

        # -------------------------------------------------------------------------
        # H. Reviewer Scoreboard Metrics HUD
        # -------------------------------------------------------------------------
        metrics_hud: Dict[str, Any] = {}
        if bundle is not None:
            metrics_hud = {
                "plan_id": plan.plan_id,
                "total_length_m": round(bundle.total_length_m, 2),
                "total_bends": bundle.total_bends,
                "total_crossings": bundle.total_crossings,
                "total_overlap_m": round(bundle.total_overlap_m, 2),
                "cost_J": round(bundle.total_cost_J, 2),
                "formula": "J = L + 5X + O + 0.5B",
                "weights": {"L": 1.0, "X": 5.0, "O": 1.0, "B": 0.5},
            }
        if partition is not None:
            metrics_hud["hub_count"] = partition.hub_count
            metrics_hud["area_gap_m2"] = round(partition.area_gap_m2, 2)

        return BIM3DScene(
            plan_id=plan.plan_id,
            units="m",
            ceiling_height_m=ceiling_height_m,
            conduit_elevation_m=conduit_elevation_m,
            elements=elements,
            metrics_hud=metrics_hud,
        )

    def export_wavefront_obj(self, scene: BIM3DScene) -> str:
        """Export a BIM3DScene into standard Wavefront OBJ format.

        Groups elements by type, assigns material declarations, and indexes
        vertices and face normals adhering to Autodesk Revit and Blender standards.

        Args:
            scene: Complete BIM3DScene model.

        Returns:
            str: Valid Wavefront OBJ text representation.
        """
        lines: List[str] = [
            "# Wavefront OBJ architectural model exported by ResPlan BIM3DGenerator",
            f"# Plan ID: {scene.plan_id}",
            f"# Units: {scene.units}",
            "mtllib materials.mtl",
            "",
        ]

        vert_offset = 1

        for elem in scene.elements:
            if not elem.vertices_3d or not elem.faces:
                continue

            lines.append(f"g {elem.element_type.value}_{elem.element_id}")
            mat_name = f"mat_{elem.element_type.value}_{elem.color_hex.lstrip('#')}"
            lines.append(f"usemtl {mat_name}")

            # 1. Output Vertices (v x y z)
            for v in elem.vertices_3d:
                lines.append(f"v {v[0]:.4f} {v[1]:.4f} {v[2]:.4f}")

            # 2. Output Vertex Normals (vn nx ny nz)
            normals = compute_vertex_normals(elem.vertices_3d, elem.faces)
            for n in normals:
                lines.append(f"vn {n[0]:.4f} {n[1]:.4f} {n[2]:.4f}")

            # 3. Output Faces (f v1//vn1 v2//vn2 v3//vn3)
            for face in elem.faces:
                tokens = [f"{idx + vert_offset}//{idx + vert_offset}" for idx in face]
                lines.append(f"f {' '.join(tokens)}")

            vert_offset += len(elem.vertices_3d)
            lines.append("")

        return "\n".join(lines)
