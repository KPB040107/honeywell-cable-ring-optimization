"""Dataset Reconciliation and Exhaustive Quarantine Audit for Honeywell PPO-ALNS.

Audits all 17,000 floor plans in ResPlan.pkl against split.json and dataset_inventory.json.
Distinguishes:
1. Mathematically infeasible plans (single eligible room > 100.0 m^2)
2. Invalid geometry / disconnected room graph / multi-component boundaries
3. Missing data / zero eligible rooms
4. Conservatively excluded plans (e.g. rooms between 95 and 100 m^2)
"""

from __future__ import annotations

import json
from pathlib import Path
import pickle
from typing import Any, Dict, List, Tuple


def audit_full_dataset() -> Dict[str, Any]:
    from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
    repo = DatasetRepository.get_instance()
    records = repo._records

    with open("split.json", "r", encoding="utf-8") as f:
        splits = json.load(f)

    with open("results/dataset_inventory.json", "r", encoding="utf-8") as f:
        inv = json.load(f)

    pindex = inv.get("plans_index", {})
    anom_list = inv.get("anomalies_and_hygiene", {}).get("anomalous_plans", [])
    anom_map = {p["plan_id"]: p["issues"] for p in anom_list}

    split_map = {}
    for split_name, pids in splits.items():
        for pid in pids:
            split_map[pid] = split_name

    categories = {
        "strictly_infeasible_room_gt_100": [],
        "tight_room_95_to_100": [],
        "zero_eligible_rooms": [],
        "multi_component_boundary": [],
        "disconnected_graph": [],
        "valid_clean_feasible": [],
    }

    per_split_counts: Dict[str, Dict[str, int]] = {
        s: {
            "total": len(pids),
            "clean_feasible": 0,
            "strictly_infeasible_gt_100": 0,
            "tight_95_to_100": 0,
            "anomalous_geometry": 0,
            "zero_rooms": 0,
        }
        for s, pids in splits.items()
    }

    for pid, plan_dict in records.items():
        sp = split_map.get(pid, "unknown")
        # Use MetricFloorPlan to get true converted metric rooms
        try:
            mplan = repo.get_floor_plan(pid)
            rooms = list(mplan.rooms.values())
            elig_rooms = [r for r in rooms if r.is_eligible]
        except Exception:
            categories["zero_eligible_rooms"].append((pid, sp))
            per_split_counts[sp]["zero_rooms"] += 1
            continue

        if not elig_rooms:
            categories["zero_eligible_rooms"].append((pid, sp))
            per_split_counts[sp]["zero_rooms"] += 1
            continue

        room_areas = [r.area_m2 for r in elig_rooms]
        max_room_area = max(room_areas) if room_areas else 0.0

        is_anom = pid in anom_map
        issues = anom_map.get(pid, [])

        if max_room_area > 100.001:
            categories["strictly_infeasible_room_gt_100"].append({
                "plan_id": pid,
                "split": sp,
                "max_room_area_m2": round(max_room_area, 2),
                "room_count": len(elig_rooms),
                "room_areas": [round(a, 1) for a in room_areas],
                "issues": issues,
            })
            per_split_counts[sp]["strictly_infeasible_gt_100"] += 1
            continue

        if 95.0 < max_room_area <= 100.001:
            categories["tight_room_95_to_100"].append({
                "plan_id": pid,
                "split": sp,
                "max_room_area_m2": round(max_room_area, 2),
                "room_count": len(elig_rooms),
                "room_areas": [round(a, 1) for a in room_areas],
            })
            per_split_counts[sp]["tight_95_to_100"] += 1

        if is_anom:
            if any("Disconnected" in iss for iss in issues):
                categories["disconnected_graph"].append((pid, sp, issues))
            else:
                categories["multi_component_boundary"].append((pid, sp, issues))
            per_split_counts[sp]["anomalous_geometry"] += 1
            continue

        categories["valid_clean_feasible"].append(pid)
        per_split_counts[sp]["clean_feasible"] += 1

    report = {
        "dataset_total": len(records),
        "split_sizes": {s: len(pids) for s, pids in splits.items()},
        "per_split_breakdown": per_split_counts,
        "strictly_infeasible_gt_100_count": len(categories["strictly_infeasible_room_gt_100"]),
        "strictly_infeasible_pids": [item["plan_id"] for item in categories["strictly_infeasible_room_gt_100"]],
        "tight_room_95_to_100_count": len(categories["tight_room_95_to_100"]),
        "tight_room_95_to_100_plans": categories["tight_room_95_to_100"],
        "zero_eligible_rooms_count": len(categories["zero_eligible_rooms"]),
        "anomalous_geometry_count": len(categories["multi_component_boundary"]) + len(categories["disconnected_graph"]),
        "clean_feasible_count": len(categories["valid_clean_feasible"]),
        "sample_strictly_infeasible": categories["strictly_infeasible_room_gt_100"][:10],
    }

    out_path = Path("results/dataset_reconciliation_audit.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("==================================================================")
    print("  EXHAUSTIVE 17,000 RESPLAN DATASET RECONCILIATION & AUDIT")
    print("==================================================================")
    print(f"Total Plans in ResPlan.pkl: {len(records):,}")
    print("\nSplit Distribution:")
    for s, pids in splits.items():
        print(f"  - {s:10s}: {len(pids):5d} plans")
    print(f"  - Total:     {sum(len(pids) for pids in splits.values()):5d} plans")

    print("\nHealth Audit Across All 17,000 Records:")
    print(f"  1. Clean, Feasible Plans:                  {report['clean_feasible_count']:5d} ({report['clean_feasible_count']/len(records)*100:.2f}%)")
    print(f"  2. Strictly Infeasible (Room > 100 m²):    {report['strictly_infeasible_gt_100_count']:5d} ({report['strictly_infeasible_gt_100_count']/len(records)*100:.2f}%)")
    print(f"  3. Feasible but Tight (Room 95-100 m²):    {report['tight_room_95_to_100_count']:5d}")
    print(f"  4. Anomalous Geometry / Disconnected:     {report['anomalous_geometry_count']:5d}")
    print(f"  5. Zero Eligible Rooms:                   {report['zero_eligible_rooms_count']:5d}")

    print("\nPer-Split Health Matrix:")
    print(f"{'Split':10s} | {'Total':5s} | {'Clean Feasible':14s} | {'Room > 100m²':12s} | {'Anom Geom':9s} | {'Zero Rooms':10s} | {'Tight 95-100':12s}")
    print("-" * 85)
    for s, b in per_split_counts.items():
        print(f"{s:10s} | {b['total']:5d} | {b['clean_feasible']:14d} | {b['strictly_infeasible_gt_100']:12d} | {b['anomalous_geometry']:9d} | {b['zero_rooms']:10d} | {b['tight_95_to_100']:12d}")

    return report


if __name__ == "__main__":
    audit_full_dataset()
