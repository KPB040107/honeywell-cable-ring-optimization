"""Configuration dataclasses and schemas for Honeywell 5-way benchmark framework.

Defines reproducible benchmark modes (smoke, validation, full, custom),
solver configurations, execution budgets, checkpoint locations, and output paths.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


class BenchmarkMode(str, Enum):
    """Execution mode for benchmark runs."""
    SMOKE = "smoke"          # 20-30 plans for fast validation
    VALIDATION = "validation"  # 100-200 plans for rigorous statistical evaluation
    TEST = "test"            # 1,632 held-out test split plans
    FULL = "full"            # All 17,000 ResPlan dataset plans
    CUSTOM = "custom"        # Explicit user-provided plan IDs


class ComplexityTier(str, Enum):
    """Floor plan complexity classification by eligible room count."""
    COMPACT = "compact"      # <= 3 eligible rooms
    STANDARD = "standard"    # 4-5 eligible rooms
    COMPLEX = "complex"      # >= 6 eligible rooms

    @classmethod
    def from_room_count(cls, count: int) -> ComplexityTier:
        """Classify room count into canonical complexity tier."""
        if count <= 3:
            return cls.COMPACT
        if count <= 5:
            return cls.STANDARD
        return cls.COMPLEX


class AlgorithmId(str, Enum):
    """Canonical identifiers for the 5 benchmarked algorithms."""
    PORTFOLIO_HYBRID = "PortfolioHybrid"
    ALNS_BANDIT = "ALNS_Bandit"
    ALNS_RANDOM = "ALNS_Random"
    LINUCB_ALNS = "LinUCB_ALNS"
    PPO_ALNS = "PPO_ALNS"


@dataclass
class AlgorithmConfig:
    """Configuration for an individual solver algorithm."""
    algo_id: str
    display_name: str
    enabled: bool = True
    max_iterations: Optional[int] = None
    checkpoint_path: Optional[str] = None
    kwargs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AlgorithmConfig:
        return cls(**data)


@dataclass
class BenchmarkConfig:
    """Master benchmark configuration."""
    mode: BenchmarkMode = BenchmarkMode.SMOKE
    plan_ids: List[int] = field(default_factory=list)
    algorithms: List[AlgorithmConfig] = field(default_factory=list)
    time_limit_seconds: float = 5.0
    max_iterations: int = 25
    random_seed: int = 42
    max_workers: int = 4
    output_dir: Path = field(default_factory=lambda: Path("results/benchmarks"))
    checkpoint_interval: int = 1
    resume: bool = True
    checkpoint_file: Optional[Path] = None
    results_csv_file: Optional[Path] = None
    tall_csv_file: Optional[Path] = None
    summary_json_file: Optional[Path] = None
    report_md_file: Optional[Path] = None
    ppo_checkpoint_path: str = "checkpoints/ppo_alns/best_model.pt"

    def __post_init__(self) -> None:
        if isinstance(self.mode, str):
            self.mode = BenchmarkMode(self.mode)
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)

        # Set default algorithms if none specified
        if not self.algorithms:
            self.algorithms = self.get_default_algorithms(
                max_iterations=self.max_iterations,
                ppo_checkpoint_path=self.ppo_checkpoint_path,
            )

        self.resolve_paths()

    def resolve_paths(self) -> None:
        """Resolve output file paths relative to output_dir if not explicitly set."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        mode_str = self.mode.value

        if self.checkpoint_file is None:
            self.checkpoint_file = self.output_dir / f"benchmark_checkpoint_{mode_str}.json"
        elif isinstance(self.checkpoint_file, str):
            self.checkpoint_file = Path(self.checkpoint_file)

        if self.results_csv_file is None:
            self.results_csv_file = self.output_dir / f"benchmark_results_{mode_str}.csv"
        elif isinstance(self.results_csv_file, str):
            self.results_csv_file = Path(self.results_csv_file)

        if self.tall_csv_file is None:
            self.tall_csv_file = self.output_dir / f"benchmark_records_tall_{mode_str}.csv"
        elif isinstance(self.tall_csv_file, str):
            self.tall_csv_file = Path(self.tall_csv_file)

        if self.summary_json_file is None:
            self.summary_json_file = self.output_dir / f"benchmark_summary_{mode_str}.json"
        elif isinstance(self.summary_json_file, str):
            self.summary_json_file = Path(self.summary_json_file)

        if self.report_md_file is None:
            self.report_md_file = Path("reports") / f"benchmark_{mode_str}_report.md"
        elif isinstance(self.report_md_file, str):
            self.report_md_file = Path(self.report_md_file)

    @staticmethod
    def get_default_algorithms(
        max_iterations: int = 25,
        ppo_checkpoint_path: str = "checkpoints/ppo_alns/best_model.pt",
    ) -> List[AlgorithmConfig]:
        """Generate the standard 5-way comparative algorithm configurations."""
        return [
            AlgorithmConfig(
                algo_id=AlgorithmId.PORTFOLIO_HYBRID.value,
                display_name="PortfolioHybrid",
                enabled=True,
                max_iterations=None,
                kwargs={},
            ),
            AlgorithmConfig(
                algo_id=AlgorithmId.ALNS_BANDIT.value,
                display_name="Standalone ALNS (Bandit)",
                enabled=True,
                max_iterations=max_iterations,
                kwargs={"max_iterations": max_iterations},
            ),
            AlgorithmConfig(
                algo_id=AlgorithmId.ALNS_RANDOM.value,
                display_name="Random ALNS",
                enabled=True,
                max_iterations=max_iterations,
                kwargs={"max_iterations": max_iterations},
            ),
            AlgorithmConfig(
                algo_id=AlgorithmId.LINUCB_ALNS.value,
                display_name="LinUCB-ALNS",
                enabled=True,
                max_iterations=max_iterations,
                kwargs={"max_iterations": max_iterations},
            ),
            AlgorithmConfig(
                algo_id=AlgorithmId.PPO_ALNS.value,
                display_name="Trained PPO-ALNS",
                enabled=True,
                max_iterations=max_iterations,
                checkpoint_path=ppo_checkpoint_path,
                kwargs={"max_steps": max_iterations, "checkpoint_path": ppo_checkpoint_path},
            ),
        ]

    def resolve_plan_ids(self) -> List[int]:
        """Resolve the target plan IDs to evaluate based on mode and configuration."""
        if self.plan_ids:
            return list(self.plan_ids)

        try:
            from rl.dataset import ResPlanDatasetManager
            dataset_mgr = ResPlanDatasetManager()
        except Exception:
            dataset_mgr = None

        if self.mode == BenchmarkMode.SMOKE:
            target_count = 25
            if dataset_mgr is not None and dataset_mgr.clean_val_ids:
                return dataset_mgr.clean_val_ids[:target_count]
            return list(range(target_count))

        elif self.mode == BenchmarkMode.VALIDATION:
            target_count = 100
            if dataset_mgr is not None:
                if len(dataset_mgr.clean_val_ids) >= target_count:
                    return dataset_mgr.clean_val_ids[:target_count]
                test_bench = dataset_mgr.get_test_benchmark_ids(count=target_count)
                if test_bench:
                    return test_bench
            return list(range(target_count))

        elif self.mode == BenchmarkMode.TEST:
            # 1,632 held-out test split plans
            if dataset_mgr is not None and getattr(dataset_mgr, "raw_test_ids", None):
                return list(dataset_mgr.raw_test_ids)
            try:
                with open("split.json", "r", encoding="utf-8") as f:
                    return list(json.load(f)["test"])
            except Exception:
                pass
            return list(range(1632))

        elif self.mode == BenchmarkMode.FULL:
            # Full 17,000 dataset evaluation across all records
            if dataset_mgr is not None:
                if hasattr(dataset_mgr, "plans_index") and dataset_mgr.plans_index:
                    return [int(k) for k in dataset_mgr.plans_index.keys()]
                if hasattr(dataset_mgr, "repo") and hasattr(dataset_mgr.repo, "record_count"):
                    return list(range(dataset_mgr.repo.record_count))
            return list(range(17000))

        # Custom or fallback
        return [0, 4, 5]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize configuration to JSON-serializable dictionary."""
        d = asdict(self)
        d["mode"] = self.mode.value
        d["output_dir"] = str(self.output_dir)
        d["checkpoint_file"] = str(self.checkpoint_file) if self.checkpoint_file else None
        d["results_csv_file"] = str(self.results_csv_file) if self.results_csv_file else None
        d["tall_csv_file"] = str(self.tall_csv_file) if self.tall_csv_file else None
        d["summary_json_file"] = str(self.summary_json_file) if self.summary_json_file else None
        d["report_md_file"] = str(self.report_md_file) if self.report_md_file else None
        d["algorithms"] = [a.to_dict() if hasattr(a, "to_dict") else a for a in self.algorithms]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BenchmarkConfig:
        """Deserialize configuration from dictionary."""
        c = dict(data)
        if "mode" in c and isinstance(c["mode"], str):
            c["mode"] = BenchmarkMode(c["mode"])
        if "output_dir" in c and isinstance(c["output_dir"], str):
            c["output_dir"] = Path(c["output_dir"])
        if "checkpoint_file" in c and isinstance(c["checkpoint_file"], str):
            c["checkpoint_file"] = Path(c["checkpoint_file"])
        if "results_csv_file" in c and isinstance(c["results_csv_file"], str):
            c["results_csv_file"] = Path(c["results_csv_file"])
        if "tall_csv_file" in c and isinstance(c["tall_csv_file"], str):
            c["tall_csv_file"] = Path(c["tall_csv_file"])
        if "summary_json_file" in c and isinstance(c["summary_json_file"], str):
            c["summary_json_file"] = Path(c["summary_json_file"])
        if "report_md_file" in c and isinstance(c["report_md_file"], str):
            c["report_md_file"] = Path(c["report_md_file"])
        if "algorithms" in c:
            c["algorithms"] = [
                AlgorithmConfig.from_dict(a) if isinstance(a, dict) else a
                for a in c["algorithms"]
            ]
        return cls(**c)
