"""Pairwise Conflict Matrix for Ring Candidates in Phase 11.

Precomputes cable crossings (X) and shared segment overlap (O) between compatible
pairs of complete ring candidates. These values are used to populate CP-SAT linearization
coefficients and ALNS incremental score updates.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set, Tuple

import shapely
from shapely.geometry import LineString, Point

from resplan_router.phase11_optimization.ring_pool import CompleteRingCandidate


def compute_ring_pair_conflict(
    ring_a: CompleteRingCandidate,
    ring_b: CompleteRingCandidate,
) -> Tuple[int, float]:
    """Compute number of crossings and total overlapping cable length between two distinct rings.

    Args:
        ring_a: First complete ring candidate.
        ring_b: Second complete ring candidate.

    Returns:
        Tuple[int, float]: (crossings_count, overlap_length_m)
    """
    # If the rings share any room, they are mutually exclusive in any valid partition
    if not ring_a.room_ids.isdisjoint(ring_b.room_ids):
        return 0, 0.0

    lines_a: List[LineString] = []
    for seg in ring_a.route_segments:
        for p1, p2 in zip(seg.polyline, seg.polyline[1:]):
            if abs(p1.x - p2.x) > 1e-4 or abs(p1.y - p2.y) > 1e-4:
                lines_a.append(LineString([(p1.x, p1.y), (p2.x, p2.y)]))

    lines_b: List[LineString] = []
    for seg in ring_b.route_segments:
        for p1, p2 in zip(seg.polyline, seg.polyline[1:]):
            if abs(p1.x - p2.x) > 1e-4 or abs(p1.y - p2.y) > 1e-4:
                lines_b.append(LineString([(p1.x, p1.y), (p2.x, p2.y)]))

    crossings = 0
    overlap_m = 0.0

    for la in lines_a:
        for lb in lines_b:
            # Fast bounding box rejection
            if not (la.bounds[2] >= lb.bounds[0] and la.bounds[0] <= lb.bounds[2] and
                    la.bounds[3] >= lb.bounds[1] and la.bounds[1] <= lb.bounds[3]):
                continue

            inter = la.intersection(lb)
            if inter.is_empty:
                continue

            # 1D collinear overlap
            if inter.geom_type in ("LineString", "MultiLineString"):
                overlap_m += inter.length
            # 0D crossing
            elif inter.geom_type == "Point":
                # Crossing counts if the point is strictly in interior of either segment,
                # or if the segments are perpendicular crossing at a point
                p = inter
                a_end = p.distance(Point(la.coords[0])) < 1e-4 or p.distance(Point(la.coords[-1])) < 1e-4
                b_end = p.distance(Point(lb.coords[0])) < 1e-4 or p.distance(Point(lb.coords[-1])) < 1e-4
                if not (a_end and b_end):
                    crossings += 1

    return crossings, round(overlap_m, 4)


class PairwiseConflictMatrix:
    """Precomputed conflict store between all pairs of candidate rings."""

    def __init__(self) -> None:
        self.conflicts: Dict[Tuple[str, str], Tuple[int, float]] = {}

    def precompute(self, candidates: Sequence[CompleteRingCandidate]) -> None:
        """Compute crossings and overlap between all compatible candidate pairs."""
        n = len(candidates)
        for i in range(n):
            c_a = candidates[i]
            for j in range(i + 1, n):
                c_b = candidates[j]
                if c_a.room_ids.isdisjoint(c_b.room_ids):
                    x_val, o_val = compute_ring_pair_conflict(c_a, c_b)
                    self.conflicts[(c_a.candidate_id, c_b.candidate_id)] = (x_val, o_val)
                    self.conflicts[(c_b.candidate_id, c_a.candidate_id)] = (x_val, o_val)

    def get_conflict(self, cand_a_id: str, cand_b_id: str) -> Tuple[int, float]:
        """Look up crossings and overlap between two candidates (returns (0, 0.0) if disjoint/none)."""
        return self.conflicts.get((cand_a_id, cand_b_id), (0, 0.0))
