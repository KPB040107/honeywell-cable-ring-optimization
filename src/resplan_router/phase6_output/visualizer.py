"""Official Slide 5 Floor-Plan Overlay & Reviewer Dashboard Visualizer.

Renders two-column architectural floor plans reproducing Slide 5 specifications:
- Left Column: Floor-plan canvas with pastel zone fills, dark charcoal boundary walls,
  open doorway thresholds, bold hub squares, node circles, axis-aligned cable routes with
  directional arrows and circled leg order numbers, plus bright crimson red violation callouts.
- Right Column: Reviewer cards with How-to-Read legend, per-hub metric table, and
  official 6-rule validation checklist.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import matplotlib
matplotlib.use("Agg")  # Headless rendering safe on macOS & servers
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, PathPatch
from matplotlib.path import Path as MplPath
import shapely
from shapely.geometry import MultiPolygon, Polygon

from resplan_router.contracts import (
    EvaluationReport,
    MetricFloorPlan,
    Point2D,
    SynthesizedRingsBundle,
)


# Architectural color palette
ZONE_PASTELS = [
    "#DCE8F8",  # Soft Blue (H1)
    "#FDE5D2",  # Soft Orange (H2)
    "#D8F3DC",  # Soft Mint (H3)
    "#FFF3B0",  # Soft Yellow (H4)
    "#F3E8FF",  # Soft Lavender (H5)
    "#FCE7F3",  # Soft Rose
    "#E0F2FE",  # Soft Sky
]

RING_COLORS = [
    "#1F77B4",  # Bold Blue
    "#E6550D",  # Bold Orange
    "#2CA02C",  # Emerald Green
    "#9467BD",  # Royal Purple
    "#D62728",  # Crimson Red
    "#8C564B",  # Chestnut
    "#17BECF",  # Cyan
]

EXCLUDED_FILL = "#F0F0F0"
EXCLUDED_EDGE = "#C0C0C0"
WALL_COLOR = "#222222"
VIOLATION_RED = "#E6194B"
PASS_GREEN = "#2E7D32"
FAIL_RED = "#D32F2F"


def _shapely_polygon_to_path(polygon: Polygon) -> MplPath:
    """Convert Shapely Polygon (with exterior and interior rings) to Matplotlib Path."""
    vertices: list[Tuple[float, float]] = []
    codes: list[int] = []

    # Exterior ring
    ext_coords = list(polygon.exterior.coords)
    if len(ext_coords) >= 3:
        vertices.extend(ext_coords)
        codes.append(MplPath.MOVETO)
        codes.extend([MplPath.LINETO] * (len(ext_coords) - 2))
        codes.append(MplPath.CLOSEPOLY)

    # Interior holes
    for interior in polygon.interiors:
        int_coords = list(interior.coords)
        if len(int_coords) >= 3:
            vertices.extend(int_coords)
            codes.append(MplPath.MOVETO)
            codes.extend([MplPath.LINETO] * (len(int_coords) - 2))
            codes.append(MplPath.CLOSEPOLY)

    return MplPath(vertices, codes)


def _draw_geometry(ax: plt.Axes, geom: Any, **kwargs: Any) -> None:
    """Draw arbitrary Shapely Polygon or MultiPolygon onto Matplotlib Axes."""
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        path = _shapely_polygon_to_path(geom)
        patch = PathPatch(path, **kwargs)
        ax.add_patch(patch)
    elif isinstance(geom, MultiPolygon):
        for poly in geom.geoms:
            path = _shapely_polygon_to_path(poly)
            patch = PathPatch(path, **kwargs)
            ax.add_patch(patch)


class FloorPlanVisualizer:
    """Renders two-column architectural floor-plan schematics matching Slide 5."""

    @classmethod
    def render_overlay(
        cls,
        plan: MetricFloorPlan,
        bundle: SynthesizedRingsBundle,
        evaluation: Optional[EvaluationReport] = None,
        output_path: Optional[Union[str, Path]] = None,
        title: Optional[str] = None,
    ) -> Figure:
        """Generate high-quality two-column figure and optionally write to disk.

        Args:
            plan: MetricFloorPlan containing rooms, obstacles, and nodes.
            bundle: SynthesizedRingsBundle containing completed rings.
            evaluation: Optional EvaluationReport from FloorPlanChecker.
            output_path: Optional file path (.png, .svg, .pdf) to save figure.
            title: Optional custom figure title.

        Returns:
            The created Matplotlib Figure instance.
        """
        # Create two-column figure (Floor Plan Canvas 70%, Sidebar 30%)
        fig, (ax_canvas, ax_sidebar) = plt.subplots(
            1, 2, figsize=(18, 10), gridspec_kw={"width_ratios": [2.3, 1.0]}
        )

        fig_title = title or f"Cable Ring Challenge — {plan.plan_id}"
        fig.suptitle(fig_title, fontsize=16, fontweight="bold", y=0.98, color="#111111")

        # 1. Render Floor Plan Canvas (Left Column)
        cls._render_canvas(ax_canvas, plan, bundle, evaluation)

        # 2. Render Reviewer Sidebar Dashboard (Right Column)
        cls._render_sidebar(ax_sidebar, plan, bundle, evaluation)

        fig.tight_layout(rect=[0, 0.02, 1, 0.95])

        if output_path is not None:
            p = Path(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(p, dpi=300, bbox_inches="tight")

        return fig

    # -------------------------------------------------------------------------
    # Canvas Rendering
    # -------------------------------------------------------------------------

    @classmethod
    def _render_canvas(
        cls,
        ax: plt.Axes,
        plan: MetricFloorPlan,
        bundle: SynthesizedRingsBundle,
        evaluation: Optional[EvaluationReport],
    ) -> None:
        """Render floor plan rooms, walls, routes, terminals, badges, and red violations."""
        ax.set_aspect("equal", adjustable="box")
        ax.set_facecolor("#FCFCFD")

        # Map each eligible room to its assigned hub index
        room_to_hub_idx: Dict[str, int] = {}
        for hub_idx, ring in enumerate(bundle.hub_rings):
            for r_id in ring.assigned_rooms:
                room_to_hub_idx[r_id] = hub_idx

        # 1. Draw Rooms & Zone Fills
        for r_id, room in plan.rooms.items():
            geom = room.geometry
            if not room.is_eligible:
                # Excluded room: neutral grey
                _draw_geometry(
                    ax,
                    geom,
                    facecolor=EXCLUDED_FILL,
                    edgecolor=EXCLUDED_EDGE,
                    linestyle="--",
                    linewidth=1.0,
                    zorder=1,
                )
                # Label
                pt = geom.representative_point()
                ax.text(
                    pt.x,
                    pt.y,
                    f"{r_id} ({room.category})\n{room.area_m2:.1f} m²\n(no node)",
                    ha="center",
                    va="center",
                    fontsize=8,
                    fontweight="medium",
                    color="#666666",
                    zorder=2,
                )
            else:
                # Eligible room: pastel zone fill
                hub_idx = room_to_hub_idx.get(r_id, 0)
                pastel = ZONE_PASTELS[hub_idx % len(ZONE_PASTELS)]
                _draw_geometry(
                    ax,
                    geom,
                    facecolor=pastel,
                    edgecolor="#B0C4DE",
                    linewidth=0.8,
                    alpha=0.85,
                    zorder=1,
                )
                pt = geom.representative_point()
                ax.text(
                    pt.x,
                    pt.y,
                    f"{r_id} ({room.category})\n{room.area_m2:.1f} m²",
                    ha="center",
                    va="center",
                    fontsize=8.5,
                    fontweight="bold",
                    color="#222222",
                    zorder=2,
                )

        # 2. Draw Obstacles (Walls & Windows) with doorway gaps open
        _draw_geometry(
            ax,
            plan.obstacles_geom,
            facecolor=WALL_COLOR,
            edgecolor="#111111",
            linewidth=1.0,
            alpha=0.92,
            zorder=3,
        )

        # 3. Draw Cable Routes and Leg Order Badges
        for hub_idx, ring in enumerate(bundle.hub_rings):
            ring_color = RING_COLORS[hub_idx % len(RING_COLORS)]
            for leg_idx, seg in enumerate(ring.route_segments):
                pts = seg.polyline
                if len(pts) < 2:
                    continue

                xs = [p.x for p in pts]
                ys = [p.y for p in pts]
                ax.plot(
                    xs,
                    ys,
                    color=ring_color,
                    linewidth=2.4,
                    solid_capstyle="round",
                    solid_joinstyle="miter",
                    zorder=4,
                )

                # Directional arrow on the longest subsegment of this polyline
                max_sub_len = -1.0
                best_sub = None
                for i in range(len(pts) - 1):
                    sub_len = math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y)
                    if sub_len > max_sub_len:
                        max_sub_len = sub_len
                        best_sub = (pts[i], pts[i + 1])

                if best_sub and max_sub_len > 0.4:
                    p1, p2 = best_sub
                    mx = (p1.x + p2.x) / 2.0
                    my = (p1.y + p2.y) / 2.0
                    ux = (p2.x - p1.x) / max_sub_len
                    uy = (p2.y - p1.y) / max_sub_len
                    ax.annotate(
                        "",
                        xy=(mx + ux * 0.18, my + uy * 0.18),
                        xytext=(mx - ux * 0.18, my - uy * 0.18),
                        arrowprops=dict(
                            arrowstyle="->",
                            color=ring_color,
                            lw=2.0,
                            mutation_scale=12,
                        ),
                        zorder=5,
                    )

                # Circled leg visiting order badge (1, 2, 3...) at segment midpoint
                total_len = seg.length_m
                half_len = total_len / 2.0
                accum = 0.0
                mid_x, mid_y = xs[len(xs) // 2], ys[len(ys) // 2]
                for i in range(len(pts) - 1):
                    d = math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y)
                    if accum + d >= half_len:
                        ratio = (half_len - accum) / max(d, 1e-6)
                        mid_x = pts[i].x + ratio * (pts[i + 1].x - pts[i].x)
                        mid_y = pts[i].y + ratio * (pts[i + 1].y - pts[i].y)
                        break
                    accum += d

                leg_num = leg_idx + 1
                ax.text(
                    mid_x,
                    mid_y,
                    str(leg_num),
                    ha="center",
                    va="center",
                    fontsize=7,
                    fontweight="bold",
                    color=ring_color,
                    bbox=dict(
                        boxstyle="circle,pad=0.22",
                        fc="white",
                        ec=ring_color,
                        lw=1.2,
                    ),
                    zorder=6,
                )

        # 4. Draw Hub Markers (Bold Squares)
        for hub_idx, ring in enumerate(bundle.hub_rings):
            ring_color = RING_COLORS[hub_idx % len(RING_COLORS)]
            hx, hy = ring.location.x, ring.location.y
            ax.scatter(
                [hx],
                [hy],
                marker="s",
                s=170,
                facecolor=ring_color,
                edgecolor="#111111",
                linewidth=1.5,
                zorder=7,
            )
            ax.text(
                hx,
                hy,
                ring.hub_id,
                ha="center",
                va="center",
                fontsize=8,
                fontweight="bold",
                color="white",
                zorder=8,
            )

        # 5. Draw Fixed Node Markers (Bold Circles)
        for node_id, node in plan.nodes.items():
            nx, ny = node.location.x, node.location.y
            ax.scatter(
                [nx],
                [ny],
                marker="o",
                s=140,
                facecolor="white",
                edgecolor="#222222",
                linewidth=1.8,
                zorder=7,
            )
            ax.text(
                nx,
                ny,
                node_id,
                ha="center",
                va="center",
                fontsize=7.5,
                fontweight="bold",
                color="#111111",
                zorder=8,
            )

        # 6. Red Violation Callout Overlay (Slide 3 Requirement)
        if evaluation is not None and not evaluation.is_valid:
            for v_idx, viol in enumerate(evaluation.violations):
                if viol.location is not None:
                    vx, vy = viol.location.x, viol.location.y
                    # Crimson violation marker
                    ax.scatter(
                        [vx],
                        [vy],
                        marker="X",
                        s=220,
                        facecolor=VIOLATION_RED,
                        edgecolor="white",
                        linewidth=1.8,
                        zorder=10,
                    )
                    # Text annotation badge
                    offset_x = 0.6 if (v_idx % 2 == 0) else -0.6
                    offset_y = 0.6 if (v_idx % 2 == 0) else -0.6
                    ax.annotate(
                        f"[Rule {viol.rule_number} VIOLATION]\n{viol.message}",
                        xy=(vx, vy),
                        xytext=(vx + offset_x, vy + offset_y),
                        arrowprops=dict(
                            arrowstyle="->",
                            color=VIOLATION_RED,
                            lw=1.8,
                        ),
                        bbox=dict(
                            boxstyle="round,pad=0.35",
                            fc="#FFF0F0",
                            ec=VIOLATION_RED,
                            lw=1.4,
                        ),
                        fontsize=7.5,
                        fontweight="bold",
                        color="#8B0000",
                        zorder=11,
                    )
                else:
                    # Non-spatial violation badge
                    ax.text(
                        0.03,
                        0.95 - v_idx * 0.06,
                        f"[Rule {viol.rule_number} VIOLATION]: {viol.message}",
                        transform=ax.transAxes,
                        fontsize=8,
                        fontweight="bold",
                        color=VIOLATION_RED,
                        bbox=dict(
                            boxstyle="round,pad=0.3",
                            fc="#FFF0F0",
                            ec=VIOLATION_RED,
                            lw=1.2,
                        ),
                        zorder=11,
                    )

        # Canvas bounds with 5% margin
        minx, miny, maxx, maxy = plan.inner_geom.bounds
        pad_x = (maxx - minx) * 0.05
        pad_y = (maxy - miny) * 0.05
        ax.set_xlim(minx - pad_x, maxx + pad_x)
        ax.set_ylim(miny - pad_y, maxy + pad_y)

        ax.set_xlabel("X (metres)", fontsize=9, color="#444444")
        ax.set_ylabel("Y (metres)", fontsize=9, color="#444444")
        ax.grid(True, linestyle=":", alpha=0.3, color="#888888")

    # -------------------------------------------------------------------------
    # Sidebar Reviewer Cards
    # -------------------------------------------------------------------------

    @classmethod
    def _render_sidebar(
        cls,
        ax: plt.Axes,
        plan: MetricFloorPlan,
        bundle: SynthesizedRingsBundle,
        evaluation: Optional[EvaluationReport],
    ) -> None:
        """Render Slide 5 Reviewer Dashboard cards, metrics table, and checklist."""
        ax.set_xlim(0, 10)
        ax.set_ylim(0, 20)
        ax.axis("off")

        # Card 1: Reviewer Verification Status (Top Banner)
        is_pass = evaluation.is_valid if evaluation else True
        status_bg = "#E8F5E9" if is_pass else "#FFEBEE"
        status_border = PASS_GREEN if is_pass else FAIL_RED
        status_text = "STATUS: VALID (100% PASS)" if is_pass else "STATUS: RULE VIOLATIONS DETECTED"
        status_color = PASS_GREEN if is_pass else FAIL_RED

        banner_box = FancyBboxPatch(
            (0.2, 18.2),
            9.6,
            1.5,
            boxstyle="round,pad=0.2,rounding_size=0.3",
            facecolor=status_bg,
            edgecolor=status_border,
            linewidth=1.8,
        )
        ax.add_patch(banner_box)
        ax.text(
            5.0,
            19.0,
            status_text,
            ha="center",
            va="center",
            fontsize=10.5,
            fontweight="bold",
            color=status_color,
        )
        sub_text = (
            f"Hubs: {len(bundle.hub_rings)} | Nodes: {len(plan.nodes)} | Area: {plan.total_area_m2:.1f} m²"
        )
        ax.text(
            5.0,
            18.5,
            sub_text,
            ha="center",
            va="center",
            fontsize=8.5,
            fontweight="medium",
            color="#333333",
        )

        # Card 2: Per-Hub Summary Metrics Table
        summary_box = FancyBboxPatch(
            (0.2, 11.2),
            9.6,
            6.6,
            boxstyle="round,pad=0.2,rounding_size=0.3",
            facecolor="#FFFFFF",
            edgecolor="#CCCCCC",
            linewidth=1.2,
        )
        ax.add_patch(summary_box)
        ax.text(
            0.5,
            17.3,
            "PER-HUB SUMMARY (Slide 5 Benchmark)",
            fontsize=9.5,
            fontweight="bold",
            color="#222222",
        )

        y_pos = 16.6
        header = f"{'Hub':<4} {'Host':<5} {'Area':<7} {'Nodes':<5} {'L (m)':<7} {'B':<3} {'J':<6}"
        ax.text(
            0.5,
            y_pos,
            header,
            fontsize=8,
            fontweight="bold",
            family="monospace",
            color="#444444",
        )
        ax.plot([0.5, 9.3], [y_pos - 0.2, y_pos - 0.2], color="#E0E0E0", lw=1)

        y_pos -= 0.6
        for hub_idx, ring in enumerate(bundle.hub_rings):
            r_color = RING_COLORS[hub_idx % len(RING_COLORS)]
            area_k = sum(
                plan.rooms[r].area_m2
                for r in ring.assigned_rooms
                if r in plan.rooms and plan.rooms[r].is_eligible
            )
            # Fetch evaluated metrics if available
            eval_hub = None
            if evaluation:
                for hm in evaluation.hub_metrics:
                    if hm.hub_id == ring.hub_id:
                        eval_hub = hm
                        break

            l_val = eval_hub.length_m if eval_hub else ring.ring_length_m
            b_val = eval_hub.bends if eval_hub else ring.ring_bends
            j_val = eval_hub.cost_J if eval_hub else ring.cost_J

            row = (
                f"{ring.hub_id:<4} {ring.room_id:<5} {area_k:>4.1f}m² "
                f"{len(ring.assigned_nodes):>3}n   {l_val:>5.1f}m {b_val:>3}  {j_val:>5.1f}"
            )
            # Hub color swatch
            ax.scatter([0.35], [y_pos + 0.1], marker="s", s=40, color=r_color)
            ax.text(
                0.5,
                y_pos,
                row,
                fontsize=8,
                fontweight="medium",
                family="monospace",
                color="#222222",
            )
            y_pos -= 0.55

        # Totals divider
        ax.plot([0.5, 9.3], [y_pos + 0.1, y_pos + 0.1], color="#E0E0E0", lw=1)
        tot_l = evaluation.total_length_m if evaluation else bundle.total_length_m
        tot_b = evaluation.total_bends if evaluation else bundle.total_bends
        tot_j = evaluation.total_cost_J if evaluation else bundle.total_cost_J
        tot_x = evaluation.total_crossings if evaluation else bundle.total_crossings
        tot_o = evaluation.total_overlap_m if evaluation else bundle.total_overlap_m
        area_gap = evaluation.area_gap_m2 if evaluation else 0.0

        total_row = f"Total: L={tot_l:.1f}m | B={tot_b} | X={tot_x} | O={tot_o:.1f}m | J={tot_j:.1f}"
        ax.text(
            0.5,
            y_pos - 0.25,
            total_row,
            fontsize=8,
            fontweight="bold",
            color="#1F77B4",
        )
        gap_row = f"Area Gap ΔA_gap: {area_gap:.1f} m²"
        ax.text(
            0.5,
            y_pos - 0.65,
            gap_row,
            fontsize=8,
            fontweight="medium",
            color="#555555",
        )

        # Card 3: What a Reviewer Confirms (Slide 3 Hard Rule Checklist)
        check_box = FancyBboxPatch(
            (0.2, 5.0),
            9.6,
            5.8,
            boxstyle="round,pad=0.2,rounding_size=0.3",
            facecolor="#FFFFFF",
            edgecolor="#CCCCCC",
            linewidth=1.2,
        )
        ax.add_patch(check_box)
        ax.text(
            0.5,
            10.3,
            "WHAT A REVIEWER CONFIRMS (Slide 3)",
            fontsize=9.5,
            fontweight="bold",
            color="#222222",
        )

        rules = [
            (
                "Rule 1",
                "All nodes served; 1 node per room",
                evaluation.rules_passed.get("Rule 1", True) if evaluation else True,
            ),
            (
                "Rule 2",
                "Max 5 nodes & <= 100 m² per hub",
                evaluation.rules_passed.get("Rule 2", True) if evaluation else True,
            ),
            (
                "Rule 3",
                "Hub in zone; rooms door-connected",
                evaluation.rules_passed.get("Rule 3", True) if evaluation else True,
            ),
            (
                "Rule 4",
                "Closed ring (hub -> nodes -> hub)",
                evaluation.rules_passed.get("Rule 4", True) if evaluation else True,
            ),
            (
                "Rule 5",
                "Axis-aligned; free space only",
                evaluation.rules_passed.get("Rule 5", True) if evaluation else True,
            ),
            (
                "Rule 6",
                "Hub clearance >= 0.5 m from walls",
                evaluation.rules_passed.get("Rule 6", True) if evaluation else True,
            ),
        ]

        cy_pos = 9.7
        for r_id, r_label, r_pass in rules:
            badge = "[PASS]" if r_pass else "[FAIL]"
            b_color = PASS_GREEN if r_pass else FAIL_RED
            ax.text(
                0.5,
                cy_pos,
                badge,
                fontsize=8,
                fontweight="bold",
                color=b_color,
            )
            ax.text(
                1.7,
                cy_pos,
                f"{r_id}: {r_label}",
                fontsize=8,
                fontweight="medium",
                color="#333333",
            )
            cy_pos -= 0.72

        # Card 4: How to Read Legend
        legend_box = FancyBboxPatch(
            (0.2, 0.4),
            9.6,
            4.2,
            boxstyle="round,pad=0.2,rounding_size=0.3",
            facecolor="#F9FAFB",
            edgecolor="#CCCCCC",
            linewidth=1.2,
        )
        ax.add_patch(legend_box)
        ax.text(
            0.5,
            4.1,
            "HOW TO READ",
            fontsize=9.5,
            fontweight="bold",
            color="#222222",
        )

        # Legend items
        ly = 3.5
        # Hub square
        ax.scatter([0.6], [ly + 0.05], marker="s", s=60, color="#1F77B4", edgecolor="#111111")
        ax.text(1.1, ly, "Hub Terminal (>= 0.5m wall clearance)", fontsize=8, color="#333333")

        ly -= 0.6
        # Node circle
        ax.scatter([0.6], [ly + 0.05], marker="o", s=50, facecolor="white", edgecolor="#222222", lw=1.5)
        ax.text(1.1, ly, "Fixed Node Terminal (1 per room)", fontsize=8, color="#333333")

        ly -= 0.6
        # Cable line + badge
        ax.plot([0.3, 0.9], [ly + 0.05, ly + 0.05], color="#1F77B4", lw=2.2)
        ax.text(
            0.6,
            ly + 0.05,
            "1",
            ha="center",
            va="center",
            fontsize=6.5,
            fontweight="bold",
            color="#1F77B4",
            bbox=dict(boxstyle="circle,pad=0.15", fc="white", ec="#1F77B4", lw=1),
        )
        ax.text(1.1, ly, "Axis-Aligned Cable & Leg Order Badge", fontsize=8, color="#333333")

        ly -= 0.6
        # Pastel zone
        p_patch = FancyBboxPatch((0.4, ly - 0.05), 0.4, 0.3, facecolor="#DCE8F8", edgecolor="#B0C4DE")
        ax.add_patch(p_patch)
        ax.text(1.1, ly, "Pastel Shading: Contiguous Hub Zone", fontsize=8, color="#333333")

        ly -= 0.6
        # Excluded grey
        ex_patch = FancyBboxPatch((0.4, ly - 0.05), 0.4, 0.3, facecolor=EXCLUDED_FILL, edgecolor=EXCLUDED_EDGE, linestyle="--")
        ax.add_patch(ex_patch)
        ax.text(1.1, ly, "Grey Hatch: Excluded Space (No Node)", fontsize=8, color="#666666")
