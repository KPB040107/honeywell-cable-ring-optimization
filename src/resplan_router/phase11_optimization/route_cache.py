"""In-memory cache for pairwise micro-routes and Hanan grid segments.

Accelerates repeated Yen's K-shortest-paths lookups and leg metric evaluations
across ALNS neighborhoods and CP-SAT ring generation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from resplan_router.contracts import CompassDirection, PairRouteMetric, Point2D


class RouteCache:
    """Cache for direction-aware routes between terminal pairs."""

    def __init__(self, maxsize: int = 10000) -> None:
        self.maxsize = maxsize
        self._cache: Dict[Tuple[str, str, Optional[str], Optional[str]], List[PairRouteMetric]] = {}
        self.hits = 0
        self.misses = 0

    def _make_key(
        self,
        u_id: str,
        v_id: str,
        d_start: Optional[CompassDirection] = None,
        d_end: Optional[CompassDirection] = None,
    ) -> Tuple[str, str, Optional[str], Optional[str]]:
        return (
            u_id,
            v_id,
            d_start.value if d_start else None,
            d_end.value if d_end else None,
        )

    def get(
        self,
        u_id: str,
        v_id: str,
        d_start: Optional[CompassDirection] = None,
        d_end: Optional[CompassDirection] = None,
    ) -> Optional[List[PairRouteMetric]]:
        key = self._make_key(u_id, v_id, d_start, d_end)
        res = self._cache.get(key)
        if res is not None:
            self.hits += 1
            return res
        self.misses += 1
        return None

    def put(
        self,
        u_id: str,
        v_id: str,
        routes: List[PairRouteMetric],
        d_start: Optional[CompassDirection] = None,
        d_end: Optional[CompassDirection] = None,
    ) -> None:
        if len(self._cache) >= self.maxsize:
            # Drop earliest 20%
            keys = list(self._cache.keys())[: self.maxsize // 5]
            for k in keys:
                del self._cache[k]

        key = self._make_key(u_id, v_id, d_start, d_end)
        self._cache[key] = routes

    def clear(self) -> None:
        self._cache.clear()
        self.hits = 0
        self.misses = 0
