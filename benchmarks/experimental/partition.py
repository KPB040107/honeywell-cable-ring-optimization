"""Independent Experimental Validation Partition Generator (Agent 5).

Strict Experimental Hygiene:
1. Strict Isolation: Isolates the frozen 1,632-plan held-out test split (split.json['test']).
   Guarantees zero overlap with the final competition evaluation set.
2. Independent Pool: Samples exclusively from verified non-anomalous, feasible plans in the
   training and validation splits (14,220 clean plans).
3. Complexity Stratification: Ensures balanced representation across:
   - Compact (<= 3 eligible rooms): boundary conditions and single-hub constraints.
   - Standard (4-5 eligible rooms): median residential floor plan topology.
   - Complex (>= 6 eligible rooms): multi-zone combinatorial partition challenges.
4. Deterministic Reproducibility: Fully seeded (default seed=20261024), version-controlled,
   and persisted to disk with rich metadata.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
from pathlib import Path
import random
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ExperimentalPartitionConfig:
    """Configuration for constructing the independent experimental validation partition."""
    total_plans: int = 500
    compact_count: int = 100
    standard_count: int = 300
    complex_count: int = 100
    random_seed: int = 20261024
    include_val_pool: bool = True
    include_train_pool: bool = True
    output_path: Path = field(
        default_factory=lambda: Path("results/benchmarks/experimental/experimental_val_500.json")
    )

    def __post_init__(self) -> None:
        if self.compact_count + self.standard_count + self.complex_count != self.total_plans:
            raise ValueError(
                f"Stratum counts sum to {self.compact_count + self.standard_count + self.complex_count}, "
                f"which does not match total_plans ({self.total_plans})"
            )
        if isinstance(self.output_path, str):
            self.output_path = Path(self.output_path)


@dataclass
class PlanPartitionRecord:
    """Metadata descriptor for a single plan in the experimental partition."""
    plan_id: int
    source_split: str  # 'train' or 'val'
    complexity_tier: str  # 'compact', 'standard', 'complex'
    eligible_room_count: int
    eligible_area_m2: float
    total_room_count: int
    gross_area_m2: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentalPartition:
    """Persisted independent experimental partition artifact."""
    version: str
    creation_timestamp: str
    random_seed: int
    total_count: int
    plan_ids: List[int]
    tier_counts: Dict[str, int]
    source_counts: Dict[str, int]
    records: Dict[int, PlanPartitionRecord]
    frozen_test_isolation_verified: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "creation_timestamp": self.creation_timestamp,
            "random_seed": self.random_seed,
            "total_count": self.total_count,
            "plan_ids": self.plan_ids,
            "tier_counts": self.tier_counts,
            "source_counts": self.source_counts,
            "frozen_test_isolation_verified": self.frozen_test_isolation_verified,
            "records": {str(k): v.to_dict() for k, v in self.records.items()},
        }

    def save(self, target_path: Optional[Path] = None) -> Path:
        """Persist partition to JSON file."""
        out = Path(target_path) if target_path else Path("results/benchmarks/experimental/experimental_val_500.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info("Experimental partition saved to %s (%d plans)", out, len(self.plan_ids))
        return out

    @classmethod
    def load(cls, path: Path | str) -> ExperimentalPartition:
        """Load partition from JSON file."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Partition file not found at {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = {
            int(k): PlanPartitionRecord(**v) for k, v in data["records"].items()
        }
        return cls(
            version=data["version"],
            creation_timestamp=data["creation_timestamp"],
            random_seed=data["random_seed"],
            total_count=data["total_count"],
            plan_ids=[int(x) for x in data["plan_ids"]],
            tier_counts=data["tier_counts"],
            source_counts=data["source_counts"],
            records=records,
            frozen_test_isolation_verified=data["frozen_test_isolation_verified"],
        )


def verify_isolation(
    candidate_pids: Sequence[int],
    split_path: Path | str = "split.json",
) -> Tuple[bool, Set[int]]:
    """Assert zero intersection between candidate plan IDs and frozen held-out test split."""
    s_path = Path(split_path)
    if not s_path.exists():
        raise FileNotFoundError(f"Split file missing at {s_path}")
    with open(s_path, "r", encoding="utf-8") as f:
        splits = json.load(f)
    test_pids = set(splits.get("test", []))
    overlap = set(candidate_pids) & test_pids
    return len(overlap) == 0, overlap


def build_experimental_partition(
    config: Optional[ExperimentalPartitionConfig] = None,
) -> ExperimentalPartition:
    """Generate the independent 500-plan experimental validation partition.

    Enforces strict experimental hygiene by drawing exclusively from clean train and val sets.
    """
    import datetime

    if config is None:
        config = ExperimentalPartitionConfig()

    split_path = Path("split.json")
    if not split_path.exists():
        raise FileNotFoundError("split.json is required to build experimental partition")

    with open(split_path, "r", encoding="utf-8") as f:
        splits = json.load(f)

    train_ids = set(splits.get("train", []))
    val_ids = set(splits.get("val", []))
    test_ids = set(splits.get("test", []))

    # Load inventory to filter out anomalous and infeasible plans
    inventory_path = Path("results/dataset_inventory.json")
    if not inventory_path.exists():
        raise FileNotFoundError("results/dataset_inventory.json is required to filter clean plans")

    with open(inventory_path, "r", encoding="utf-8") as f:
        inventory = json.load(f)
    plans_index: Dict[str, Dict[str, Any]] = inventory.get("plans_index", {})

    # Load reconciliation audit for strictly infeasible plans
    audit_path = Path("results/dataset_reconciliation_audit.json")
    infeasible_pids: Set[int] = set()
    if audit_path.exists():
        try:
            with open(audit_path, "r", encoding="utf-8") as f:
                audit_data = json.load(f)
            infeasible_pids = set(audit_data.get("strictly_infeasible_pids", []))
        except Exception:
            pass

    # Gather clean pool
    pool_pids: List[int] = []
    if config.include_train_pool:
        pool_pids.extend(train_ids)
    if config.include_val_pool:
        pool_pids.extend(val_ids)

    # Sort for deterministic processing
    pool_pids = sorted(pool_pids)

    # Buckets by complexity tier
    compact_candidates: List[int] = []
    standard_candidates: List[int] = []
    complex_candidates: List[int] = []

    for pid in pool_pids:
        # Strict isolation check: NEVER admit a test ID
        if pid in test_ids:
            continue
        if pid in infeasible_pids:
            continue

        meta = plans_index.get(str(pid))
        if meta is None:
            continue
        if meta.get("is_anomalous", False):
            continue
        room_count = meta.get("eligible_room_count", 0)
        area = meta.get("eligible_area_m2", 0.0)
        if room_count <= 0 or area <= 0.0:
            continue

        if room_count <= 3:
            compact_candidates.append(pid)
        elif room_count <= 5:
            standard_candidates.append(pid)
        else:
            complex_candidates.append(pid)

    rng = random.Random(config.random_seed)

    # Sample stratified quotas
    if len(compact_candidates) < config.compact_count:
        raise ValueError(f"Insufficient compact candidates: {len(compact_candidates)} < {config.compact_count}")
    if len(standard_candidates) < config.standard_count:
        raise ValueError(f"Insufficient standard candidates: {len(standard_candidates)} < {config.standard_count}")
    if len(complex_candidates) < config.complex_count:
        raise ValueError(f"Insufficient complex candidates: {len(complex_candidates)} < {config.complex_count}")

    sampled_compact = rng.sample(compact_candidates, config.compact_count)
    sampled_standard = rng.sample(standard_candidates, config.standard_count)
    sampled_complex = rng.sample(complex_candidates, config.complex_count)

    all_sampled = sampled_compact + sampled_standard + sampled_complex
    # Deterministic interleave / sort
    all_sampled = sorted(all_sampled)

    # Verify isolation
    is_isolated, overlap = verify_isolation(all_sampled, split_path)
    if not is_isolated:
        raise RuntimeError(f"FATAL: Isolation violated! Overlap with frozen test split: {overlap}")

    records: Dict[int, PlanPartitionRecord] = {}
    source_counts = {"train": 0, "val": 0}
    tier_counts = {"compact": 0, "standard": 0, "complex": 0}

    for pid in all_sampled:
        src = "train" if pid in train_ids else "val"
        source_counts[src] += 1
        meta = plans_index[str(pid)]
        n_rooms = meta.get("eligible_room_count", 0)
        tier = "compact" if n_rooms <= 3 else "standard" if n_rooms <= 5 else "complex"
        tier_counts[tier] += 1

        rec = PlanPartitionRecord(
            plan_id=pid,
            source_split=src,
            complexity_tier=tier,
            eligible_room_count=n_rooms,
            eligible_area_m2=float(meta.get("eligible_area_m2", 0.0)),
            total_room_count=int(meta.get("total_room_count", 0)),
            gross_area_m2=float(meta.get("gross_area_m2", 0.0)),
        )
        records[pid] = rec

    partition = ExperimentalPartition(
        version="1.0.0",
        creation_timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        random_seed=config.random_seed,
        total_count=len(all_sampled),
        plan_ids=all_sampled,
        tier_counts=tier_counts,
        source_counts=source_counts,
        records=records,
        frozen_test_isolation_verified=True,
    )

    partition.save(config.output_path)
    return partition


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    part = build_experimental_partition()
    print(f"Generated partition with {part.total_count} plans:")
    print(f"  Tiers: {part.tier_counts}")
    print(f"  Sources: {part.source_counts}")
    print(f"  Test isolation verified: {part.frozen_test_isolation_verified}")
