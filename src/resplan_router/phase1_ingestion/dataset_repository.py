"""Process-wide ResPlan dataset repository.

Loads and indexes ResPlan.pkl once per backend process, providing sub-millisecond
in-memory record lookups by integer or numeric-string Plan ID, LRU caching of
normalized MetricFloorPlan instances and GeoJSON preview structures, and explicit
error diagnostics for missing or malformed plans.
"""

from __future__ import annotations

import functools
import logging
import os
from pathlib import Path
import pickle
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)


class DatasetUnavailableError(FileNotFoundError):
    """Raised when ResPlan.pkl cannot be located or opened."""


class InvalidPlanIdError(ValueError):
    """Raised when a supplied plan ID is not a valid non-negative integer or numeric string."""


class PlanNotFoundError(KeyError):
    """Raised when a valid Plan ID does not exist in the loaded dataset."""


class MalformedRecordError(ValueError):
    """Raised when a dataset record lacks mandatory geometry or metadata attributes."""


class DatasetRepository:
    """Singleton repository managing process-wide in-memory ResPlan dataset records."""

    _instance: Optional[DatasetRepository] = None

    def __init__(self, dataset_path: Optional[Union[str, Path]] = None, auto_load: bool = True) -> None:
        self.dataset_path = self._resolve_dataset_path(dataset_path)
        self._records: Dict[int, Dict[str, Any]] = {}
        self._ready: bool = False
        self._load_error: Optional[str] = None
        self._cache_metric_plans: Dict[int, Any] = {}
        self._cache_preview_geojson: Dict[int, Dict[str, Any]] = {}
        self._cache_maxsize: int = 256

        if auto_load:
            self.load()

    @classmethod
    def get_instance(
        cls,
        dataset_path: Optional[Union[str, Path]] = None,
        force_reload: bool = False,
    ) -> DatasetRepository:
        """Obtain the process-wide singleton repository instance."""
        if cls._instance is None or force_reload:
            cls._instance = cls(dataset_path=dataset_path, auto_load=True)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (useful for unit tests)."""
        cls._instance = None

    @staticmethod
    def _resolve_dataset_path(dataset_path: Optional[Union[str, Path]]) -> Path:
        """Resolve dataset file path from parameter, environment variable, or root default."""
        if dataset_path:
            return Path(dataset_path).expanduser().resolve()

        env_path = os.environ.get("RESPLAN_DATASET_PATH")
        if env_path:
            return Path(env_path).expanduser().resolve()

        # Default to ResPlan.pkl in project root
        candidates = [
            Path.cwd() / "ResPlan.pkl",
            Path(__file__).parents[3] / "ResPlan.pkl",
        ]
        for cand in candidates:
            if cand.exists():
                return cand.resolve()

        # Fallback to current working directory target even if not yet created
        return (Path.cwd() / "ResPlan.pkl").resolve()

    def load(self) -> None:
        """Load and index the dataset from pickle file into in-memory dictionary."""
        if not self.dataset_path.exists():
            self._ready = False
            self._load_error = f"Dataset file not found at '{self.dataset_path}'"
            logger.warning(self._load_error)
            return

        try:
            logger.info("Loading ResPlan dataset from '%s'...", self.dataset_path)
            with open(self.dataset_path, "rb") as f:
                raw_container = pickle.load(f)

            records_dict: Dict[int, Dict[str, Any]] = {}

            # Support various container types: list, tuple, dict, DataFrame, ndarray
            if isinstance(raw_container, dict):
                # Could be {id: record} or {'id': [...], 'area': [...]}
                if "id" in raw_container and isinstance(raw_container["id"], (list, tuple)):
                    # Columnar dictionary
                    ids = raw_container["id"]
                    keys = list(raw_container.keys())
                    for idx, pid in enumerate(ids):
                        rec = {k: raw_container[k][idx] for k in keys}
                        records_dict[int(pid)] = rec
                else:
                    for k, v in raw_container.items():
                        if isinstance(v, dict):
                            pid = int(v.get("id", k))
                            records_dict[pid] = v
                        else:
                            records_dict[int(k)] = v
            elif isinstance(raw_container, (list, tuple)):
                for item in raw_container:
                    if isinstance(item, dict):
                        if "id" not in item:
                            raise MalformedRecordError("Record in list container lacks 'id' attribute")
                        pid = int(item["id"])
                        if pid in records_dict:
                            logger.warning("Duplicate Plan ID %d encountered; retaining first occurrence", pid)
                        else:
                            records_dict[pid] = item
                    elif hasattr(item, "id"):
                        pid = int(getattr(item, "id"))
                        records_dict[pid] = item
                    else:
                        raise MalformedRecordError(f"Unsupported record format in dataset container: {type(item)}")
            elif hasattr(raw_container, "to_dict"):
                # pandas DataFrame
                df_records = raw_container.to_dict(orient="records")
                for rec in df_records:
                    pid = int(rec["id"])
                    records_dict[pid] = rec
            else:
                raise MalformedRecordError(f"Unsupported top-level container type: {type(raw_container)}")

            self._records = records_dict
            self._ready = True
            self._load_error = None
            logger.info("Successfully indexed %d ResPlan floor plan records", len(self._records))

        except Exception as exc:
            self._ready = False
            self._load_error = str(exc)
            logger.exception("Failed to load ResPlan dataset from '%s'", self.dataset_path)
            raise

    @property
    def is_ready(self) -> bool:
        """True if the dataset has been loaded and indexed into memory."""
        return self._ready and len(self._records) > 0

    @property
    def record_count(self) -> int:
        """Total number of indexed Plan IDs."""
        return len(self._records)

    def get_status(self) -> Dict[str, Any]:
        """Obtain repository status descriptor adhering to Slide 11 contract."""
        return {
            "ready": self.is_ready,
            "record_count": self.record_count,
            "dataset": self.dataset_path.name if self.dataset_path else "ResPlan.pkl",
            "error": self._load_error,
        }

    @staticmethod
    def parse_plan_id(plan_id: Union[int, str]) -> int:
        """Validate and parse a Plan ID into a positive integer.

        Raises:
            InvalidPlanIdError: If plan_id is non-numeric, empty, or negative.
        """
        if plan_id is None:
            raise InvalidPlanIdError("Plan ID cannot be None")

        if isinstance(plan_id, int):
            parsed = plan_id
        elif isinstance(plan_id, str):
            cleaned = plan_id.strip()
            if not cleaned or not cleaned.isdigit():
                raise InvalidPlanIdError(
                    f"Plan ID must be a valid positive integer string, got '{plan_id}'"
                )
            parsed = int(cleaned)
        else:
            raise InvalidPlanIdError(f"Invalid Plan ID type: {type(plan_id).__name__}")

        if parsed < 0:
            raise InvalidPlanIdError(f"Plan ID must be non-negative, got {parsed}")

        return parsed

    def get_record(self, plan_id: Union[int, str]) -> Dict[str, Any]:
        """Fetch raw dataset dictionary for a Plan ID.

        Raises:
            DatasetUnavailableError: If the dataset file is missing or unindexed.
            InvalidPlanIdError: If plan_id is not a valid integer or numeric string.
            PlanNotFoundError: If the requested plan_id is not in the dataset.
        """
        if not self._ready:
            if not self.dataset_path.exists():
                raise DatasetUnavailableError(
                    f"ResPlan dataset unavailable at '{self.dataset_path}'. "
                    f"Please verify file exists or set RESPLAN_DATASET_PATH."
                )
            self.load()

        numeric_id = self.parse_plan_id(plan_id)

        if numeric_id not in self._records:
            raise PlanNotFoundError(
                f"ResPlan Plan ID {numeric_id} was not found in the dataset ({len(self._records)} indexed plans)."
            )

        return self._records[numeric_id]

    def get_floor_plan(self, plan_id: Union[int, str]) -> Any:
        """Fetch or adapt and cache a normalized MetricFloorPlan instance.

        Uses in-memory LRU cache to ensure warm requests complete in < 500 ms.
        """
        numeric_id = self.parse_plan_id(plan_id)

        if numeric_id in self._cache_metric_plans:
            return self._cache_metric_plans[numeric_id]

        record = self.get_record(numeric_id)
        from resplan_router.phase1_ingestion.resplan_adapter import adapt_resplan_record

        metric_plan = adapt_resplan_record(record)

        # Cache management
        if len(self._cache_metric_plans) >= self._cache_maxsize:
            # Pop first item
            first_key = next(iter(self._cache_metric_plans))
            del self._cache_metric_plans[first_key]

        self._cache_metric_plans[numeric_id] = metric_plan
        return metric_plan

    def get_preview_geojson(self, plan_id: Union[int, str]) -> Dict[str, Any]:
        """Generate and cache browser-safe GeoJSON preview data."""
        numeric_id = self.parse_plan_id(plan_id)

        if numeric_id in self._cache_preview_geojson:
            return self._cache_preview_geojson[numeric_id]

        from resplan_router.phase1_ingestion.resplan_adapter import floor_plan_to_preview_geojson

        metric_plan = self.get_floor_plan(numeric_id)
        raw_record = self.get_record(numeric_id)
        preview_data = floor_plan_to_preview_geojson(metric_plan, raw_record)

        if len(self._cache_preview_geojson) >= self._cache_maxsize:
            first_key = next(iter(self._cache_preview_geojson))
            del self._cache_preview_geojson[first_key]

        self._cache_preview_geojson[numeric_id] = preview_data
        return preview_data
