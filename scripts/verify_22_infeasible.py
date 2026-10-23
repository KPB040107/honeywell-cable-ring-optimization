"""Script to inspect and verify the 22 physically infeasible plans."""
import json
import pickle
import math
from pathlib import Path
import shapely
from shapely.geometry import Polygon, MultiPolygon, Point
from shapely.ops import unary_union

from resplan_router.phase1_ingestion.resplan_adapter import adapt_resplan_record

with open("ResPlan.pkl", "rb") as f:
    resplan_list = pickle.load(f)

resplan_map = {r["id"]: r for r in resplan_list}

rank1_pids = [5460, 15000, 15678, 145814, 227794, 284019, 301603, 345536, 399279, 413814, 586490, 589710, 622584, 952547]
rank2_pids = [452, 778, 4048, 7447, 8272, 8716, 16162, 16612]

print(f"Total plans in ResPlan: {len(resplan_map)}")

print("\n--- Verifying Rank 1 (14 plans: Hub clearance < 0.50m) ---")
for pid in rank1_pids:
    if pid not in resplan_map:
        print(f"PID {pid} NOT FOUND!")
        continue
    rec = resplan_map[pid]
    plan = adapt_resplan_record(rec)
    inner_poly = shapely.from_wkt(plan.inner_wkt)
    # Check max clearance in each eligible room
    max_room_clearances = {}
    for rid, r in plan.rooms.items():
        if not r.is_eligible:
            continue
        rpoly = shapely.from_wkt(r.polygon_wkt)
        # Compute maximum distance from boundary within the room (maximum inscribed circle radius)
        # We can approximate with negative buffer or grid sampling
        # Binary search on buffer distance
        low, high = 0.0, 5.0
        while high - low > 0.001:
            mid = (low + high) / 2.0
            shrunk = rpoly.buffer(-mid)
            if shrunk.is_empty:
                high = mid
            else:
                low = mid
        max_room_clearances[rid] = round(low, 4)
    
    print(f"Plan {pid:6d}: {len(plan.rooms)} rooms ({len(max_room_clearances)} eligible), max clearance per room: {max_room_clearances}")

print("\n--- Verifying Rank 2 (8 plans: Isolated degree-0 leaf rooms) ---")
for pid in rank2_pids:
    if pid not in resplan_map:
        print(f"PID {pid} NOT FOUND!")
        continue
    rec = resplan_map[pid]
    plan = adapt_resplan_record(rec)
    door_wkt = plan.doors_wkt
    doors = shapely.from_wkt(door_wkt) if door_wkt else None
    
    # Check each eligible room's intersection with doors
    isolated_rooms = []
    for rid, r in plan.rooms.items():
        if not r.is_eligible:
            continue
        rpoly = shapely.from_wkt(r.polygon_wkt)
        # Check door intersection
        has_door = False
        if doors is not None and not doors.is_empty:
            # Check if door touches or intersects room
            if doors.intersects(rpoly.buffer(0.05)):
                has_door = True
        if not has_door:
            isolated_rooms.append((rid, r.area_m2))
    print(f"Plan {pid:6d}: {len(plan.rooms)} rooms, isolated eligible rooms: {isolated_rooms}")
