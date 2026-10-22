"""Dataset Loader and Curriculum Split Manager for PPO-ALNS (Subagent 4).

Manages reproducible splits (train, val, test) and curriculum strata for the
17,000 ResPlan dataset, filtering malformed records and providing fast sampling.
"""

from __future__ import annotations

import json
from pathlib import Path
import random
from typing import Any, Dict, List, Optional, Tuple

from resplan_router.contracts import MetricFloorPlan
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository


INVENTORY_PATH = Path("results/dataset_inventory.json")
SPLIT_PATH = Path("split.json")


class ResPlanDatasetManager:
    """Manages floor plan datasets, splits, and curriculum buckets."""

    def __init__(
        self,
        inventory_path: Path = INVENTORY_PATH,
        split_path: Path = SPLIT_PATH,
    ) -> None:
        self.inventory_path = inventory_path
        self.split_path = split_path
        self.repo = DatasetRepository.get_instance()

        self._load_inventory_and_splits()

    def _load_inventory_and_splits(self) -> None:
        if self.inventory_path.exists():
            with open(self.inventory_path, "r", encoding="utf-8") as f:
                inv = json.load(f)
            self.plans_index: Dict[str, Dict[str, Any]] = inv.get("plans_index", {})
        else:
            self.plans_index = {}

        if self.split_path.exists():
            with open(self.split_path, "r", encoding="utf-8") as f:
                splits = json.load(f)
            self.raw_train_ids: List[int] = splits.get("train", [])
            self.raw_val_ids: List[int] = splits.get("val", [])
            self.raw_test_ids: List[int] = splits.get("test", [])
        else:
            self.raw_train_ids = []
            self.raw_val_ids = []
            self.raw_test_ids = []

        # Load strictly infeasible plan IDs from audit if available
        audit_path = Path("results/dataset_reconciliation_audit.json")
        strictly_infeasible_set: set[int] = set()
        if audit_path.exists():
            try:
                with open(audit_path, "r", encoding="utf-8") as f:
                    audit_data = json.load(f)
                strictly_infeasible_set = set(audit_data.get("strictly_infeasible_pids", []))
            except Exception:
                pass

        # Filter out anomalous / malformed / strictly infeasible plans
        self.quarantined_plans: Dict[int, str] = {}
        self.clean_train_ids: List[int] = []

        for pid in self.raw_train_ids:
            meta = self.plans_index.get(str(pid))
            if meta is not None:
                if meta.get("is_anomalous", False):
                    self.quarantined_plans[pid] = "Anomalous geometry / disconnected graph"
                    continue
                if meta.get("eligible_room_count", 0) <= 0:
                    self.quarantined_plans[pid] = "Zero eligible rooms"
                    continue
                if meta.get("eligible_area_m2", 0) <= 0.0:
                    self.quarantined_plans[pid] = "Zero eligible area"
                    continue
                if pid in strictly_infeasible_set:
                    self.quarantined_plans[pid] = "Mathematically infeasible: single room > 100.0 m² exceeds hub capacity"
                    continue
            self.clean_train_ids.append(pid)

        self.clean_val_ids: List[int] = []
        for pid in self.raw_val_ids:
            meta = self.plans_index.get(str(pid), {})
            if meta.get("is_anomalous", False) or meta.get("eligible_room_count", 0) <= 0:
                self.quarantined_plans[pid] = "Validation anomalous / zero rooms"
                continue
            if pid in strictly_infeasible_set:
                self.quarantined_plans[pid] = "Validation infeasible: room > 100.0 m²"
                continue
            self.clean_val_ids.append(pid)

        self.clean_test_ids: List[int] = []
        for pid in self.raw_test_ids:
            meta = self.plans_index.get(str(pid), {})
            if meta.get("is_anomalous", False) or meta.get("eligible_room_count", 0) <= 0:
                self.quarantined_plans[pid] = "Test anomalous / zero rooms"
                continue
            if pid in strictly_infeasible_set:
                self.quarantined_plans[pid] = "Test infeasible: room > 100.0 m²"
                continue
            self.clean_test_ids.append(pid)

        # Categorize train plans by room count curriculum strata
        self.curriculum_strata: Dict[str, List[int]] = {
            "compact": [],   # 1-3 rooms
            "standard": [],  # 4-5 rooms
            "complex": [],   # 6-9 rooms
            "all": self.clean_train_ids,
        }

        for pid in self.clean_train_ids:
            meta = self.plans_index.get(str(pid))
            n_rooms = meta.get("eligible_room_count", 0) if meta else 0
            if n_rooms <= 3:
                self.curriculum_strata["compact"].append(pid)
            elif n_rooms <= 5:
                self.curriculum_strata["standard"].append(pid)
            else:
                self.curriculum_strata["complex"].append(pid)

    def get_plan(self, plan_id: int | str) -> MetricFloorPlan:
        """Retrieve MetricFloorPlan instance via process-wide cache."""
        return self.repo.get_floor_plan(plan_id)

    def sample_plan_id(self, stratum: str = "all", rng: Optional[random.Random] = None) -> int:
        """Sample a plan ID uniformly from specified curriculum stratum."""
        candidates = self.curriculum_strata.get(stratum, self.clean_train_ids)
        if not candidates:
            candidates = self.clean_train_ids
        if rng is not None:
            return rng.choice(candidates)
        return random.choice(candidates)

    def get_test_benchmark_ids(self, count: int = 100) -> List[int]:
        """Return deterministic test plan IDs for comparative benchmarking."""
        # Check if stratified test list exists in dataset_inventory
        if self.inventory_path.exists():
            with open(self.inventory_path, "r", encoding="utf-8") as f:
                inv = json.load(f)
            bench = inv.get("benchmarks", {})
            st2 = bench.get("stage_2_stratified_100", [])
            if len(st2) >= count:
                return [int(x) for x in st2[:count]]

        return self.clean_test_ids[:count]

    def is_plan_quarantined(self, plan_id: int) -> bool:
        """Check if a plan has been marked as quarantined."""
        return plan_id in self.quarantined_plans

    def save_quarantine_log(self, output_path: str = "logs/quarantined_plans.json") -> None:
        """Save machine-readable inventory of quarantined plans and reasons."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        summary = {
            "total_quarantined": len(self.quarantined_plans),
            "quarantined_records": self.quarantined_plans,
        }
        with open(out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
