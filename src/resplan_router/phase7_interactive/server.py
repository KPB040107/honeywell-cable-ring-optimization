"""FastAPI WebSockets Server for Real-Time Floor-Plan Co-Design Canvas.

Provides sub-50 ms bidirectional WebSocket streaming (/ws/co-design) and REST APIs
for real-time floor plan mutations, Slide 8 JSON export, asynchronous optimization jobs,
dual-oracle verification, dataset browsing, and floor plan geometry inspections.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import uuid

from fastapi import (
    APIRouter,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import matplotlib.pyplot as plt
from pydantic import BaseModel, Field
import shapely.wkt

from resplan_router.api import OptimizationConfig, optimize_plan
from resplan_router.experimental.independent_verifier import IndependentVerifier
from resplan_router.phase1_ingestion.dataset_repository import (
    DatasetRepository,
    DatasetUnavailableError,
    InvalidPlanIdError,
    MalformedRecordError,
    PlanNotFoundError,
)
from resplan_router.phase1_ingestion.resplan_adapter import adapt_resplan_record
from resplan_router.phase1_ingestion.validator import validate_floor_plan_json
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase6_output.visualizer import FloorPlanVisualizer
from resplan_router.phase7_interactive.export_service import ExportService, create_export_router
from resplan_router.phase7_interactive.incremental_router import IncrementalCoDesignEngine

logger = logging.getLogger(__name__)

# Enforce strict file size limits (<= 10 MB)
MAX_PAYLOAD_BYTES = 10 * 1024 * 1024  # 10 MB limit

# Global singleton engine instance
engine = IncrementalCoDesignEngine()

app = FastAPI(
    title="ResPlan Real-Time Co-Design & Optimization API",
    description="Sub-50 ms incremental rectilinear re-routing, async optimization jobs, dual-oracle certification, and dataset census HUD",
    version="2.0.0",
)

# Enable CORS for local web clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic Request Models
# ---------------------------------------------------------------------------

class NodeDragRequest(BaseModel):
    node_id: str
    x: float
    y: float


class HubDragRequest(BaseModel):
    hub_id: str
    x: float
    y: float


class DoorSlideRequest(BaseModel):
    door_id: str
    offset_m: float = 0.0


class WallShiftRequest(BaseModel):
    room_id: str
    delta_area_m2: float = 0.0


class SuggestRequest(BaseModel):
    max_proposals: int = 5


class ProposalApplyRequest(BaseModel):
    proposal_id: str


class WireCutRequest(BaseModel):
    hub_id: Optional[str] = "H1"
    cut_x: float
    cut_y: float


class OptimizationJobRequest(BaseModel):
    plan_id: Optional[Union[str, int]] = None
    plan_data: Optional[Dict[str, Any]] = None
    algorithm: Optional[str] = "production_fallback"
    seed: Optional[int] = 42
    timeout: Optional[float] = 30.0
    max_iterations: Optional[int] = 25


class DirectOptimizeRequest(BaseModel):
    plan_id: Optional[Union[str, int]] = None
    plan_data: Optional[Dict[str, Any]] = None
    algorithm: Optional[str] = "production_fallback"
    seed: Optional[int] = 42
    timeout: Optional[float] = 30.0
    max_iterations: Optional[int] = 25


class ValidationRequest(BaseModel):
    plan_id: Optional[Union[str, int]] = None
    plan_data: Optional[Dict[str, Any]] = None
    solution: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Background Job Execution Manager
# ---------------------------------------------------------------------------

class JobStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OptimizationJobManager:
    """Thread-safe background job runner for asynchronous floor plan optimizations with deduplication."""

    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="resplan_opt")
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._active_plan_jobs: Dict[str, str] = {}  # dedup_key -> job_id
        self._lock = threading.Lock()

    def submit_job(
        self,
        plan_input: Any,
        plan_id: str,
        algorithm: str = "production_fallback",
        seed: int = 42,
        timeout: float = 30.0,
        max_iterations: int = 25,
        dedup_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            key = dedup_key or f"plan_{plan_id}"
            if key in self._active_plan_jobs:
                existing_id = self._active_plan_jobs[key]
                existing_job = self._jobs.get(existing_id)
                if existing_job and existing_job["status"] in (JobStatus.QUEUED, JobStatus.RUNNING):
                    logger.info("Deduplicated optimization job for plan '%s' (reusing %s)", plan_id, existing_id)
                    return {
                        "job_id": existing_id,
                        "status": existing_job["status"],
                        "plan_id": existing_job["plan_id"],
                        "algorithm": existing_job["algorithm"],
                        "stage": existing_job["stage"],
                        "submitted_at": existing_job["submitted_at"],
                        "message": f"Optimization job for plan '{plan_id}' is already {existing_job['status'].lower()}",
                        "deduplicated": True,
                    }

            job_id = str(uuid.uuid4())
            now_iso = datetime.now(timezone.utc).isoformat()
            job_record: Dict[str, Any] = {
                "job_id": job_id,
                "plan_id": plan_id,
                "dedup_key": key,
                "status": JobStatus.QUEUED,
                "stage": "Queued in background worker pool",
                "algorithm": algorithm,
                "seed": seed,
                "timeout": timeout,
                "max_iterations": max_iterations,
                "submitted_at": now_iso,
                "started_at": None,
                "completed_at": None,
                "elapsed_s": 0.0,
                "result": None,
                "error": None,
                "cancelled": False,
            }
            self._jobs[job_id] = job_record
            self._active_plan_jobs[key] = job_id

            future = self._executor.submit(
                self._worker_execute,
                job_id=job_id,
                plan_input=plan_input,
                algorithm=algorithm,
                seed=seed,
                timeout=timeout,
                max_iterations=max_iterations,
                dedup_key=key,
            )
            job_record["_future"] = future

            return {
                "job_id": job_id,
                "status": JobStatus.QUEUED,
                "plan_id": plan_id,
                "algorithm": algorithm,
                "stage": job_record["stage"],
                "submitted_at": now_iso,
                "message": f"Optimization job {job_id} submitted and queued successfully",
                "deduplicated": False,
            }

    def _worker_execute(
        self,
        job_id: str,
        plan_input: Any,
        algorithm: str,
        seed: int,
        timeout: float,
        max_iterations: int,
        dedup_key: str,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("cancelled"):
                if job:
                    job["status"] = JobStatus.CANCELLED
                    job["stage"] = "Cancelled before execution"
                self._active_plan_jobs.pop(dedup_key, None)
                return
            job["status"] = JobStatus.RUNNING
            job["started_at"] = datetime.now(timezone.utc).isoformat()
            job["stage"] = "Executing optimization pipeline"

        t0 = time.perf_counter()
        try:
            cfg = OptimizationConfig(
                algorithm=algorithm,
                random_seed=seed,
                time_limit_seconds=timeout,
                max_iterations=max_iterations,
                export_slide8=True,
            )
            result = optimize_plan(plan_input, config=cfg)
            elapsed = time.perf_counter() - t0

            with self._lock:
                if job.get("cancelled"):
                    job["status"] = JobStatus.CANCELLED
                    job["stage"] = "Cancelled during execution"
                    job["elapsed_s"] = round(elapsed, 3)
                else:
                    job["status"] = JobStatus.COMPLETED
                    job["stage"] = "Completed successfully"
                    job["completed_at"] = datetime.now(timezone.utc).isoformat()
                    job["elapsed_s"] = round(elapsed, 3)
                    job["result"] = result.model_dump()
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            logger.exception("Background optimization job %s failed: %s", job_id, exc)
            with self._lock:
                job["status"] = JobStatus.FAILED
                job["stage"] = "Execution failed"
                job["completed_at"] = datetime.now(timezone.utc).isoformat()
                job["elapsed_s"] = round(elapsed, 3)
                job["error"] = str(exc)
        finally:
            with self._lock:
                self._active_plan_jobs.pop(dedup_key, None)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            copy_job = {k: v for k, v in job.items() if not k.startswith("_")}
            if copy_job["status"] == JobStatus.RUNNING and job.get("started_at"):
                try:
                    start_dt = datetime.fromisoformat(job["started_at"])
                    copy_job["elapsed_s"] = round((datetime.now(timezone.utc) - start_dt).total_seconds(), 3)
                except Exception:
                    pass
            return copy_job

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return {"found": False, "status": "NOT_FOUND", "message": f"Job '{job_id}' not found"}

            if job["status"] in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                return {
                    "found": True,
                    "status": job["status"],
                    "message": f"Job '{job_id}' has already finished with status {job['status']}",
                }

            job["cancelled"] = True
            fut = job.get("_future")
            if fut and not fut.running():
                fut.cancel()

            job["status"] = JobStatus.CANCELLED
            job["stage"] = "Job cancelled by client request"
            job["completed_at"] = datetime.now(timezone.utc).isoformat()
            self._active_plan_jobs.pop(job.get("dedup_key", ""), None)
            return {
                "found": True,
                "status": JobStatus.CANCELLED,
                "message": f"Job '{job_id}' was successfully cancelled",
            }


job_manager = OptimizationJobManager(max_workers=4)


# ---------------------------------------------------------------------------
# Helper Functions: Plan Resolution & Request Parsing
# ---------------------------------------------------------------------------

def resolve_metric_floor_plan(plan_identifier: Union[int, str]) -> Any:
    """Resolve MetricFloorPlan instance from current engine, DatasetRepository, or demo plans."""
    if engine.plan and str(engine.plan.plan_id) == str(plan_identifier):
        return engine.plan

    repo = DatasetRepository.get_instance()
    try:
        return repo.get_floor_plan(plan_identifier)
    except Exception:
        pass

    demo_aliases = {
        "slide5": "demo_slide5",
        "demo_slide5": "demo_slide5",
        "simple": "demo_simple",
        "demo_simple": "demo_simple",
        "compact": "demo_compact",
        "demo_compact": "demo_compact",
    }
    alias = demo_aliases.get(str(plan_identifier).lower())
    if alias:
        from resplan_router.phase1_ingestion.benchmarks import get_benchmark_plan
        return get_benchmark_plan(alias)

    raise HTTPException(
        status_code=404,
        detail=f"Floor plan '{plan_identifier}' was not found in loaded dataset or demo benchmarks",
    )


async def parse_optimization_request(
    request: Request,
) -> Tuple[Any, str, str, int, float, int, Optional[str]]:
    """Parse optimization request from JSON or multipart form, enforcing <= 10MB and safe JSON.

    Returns:
        (plan_input, plan_id_str, algorithm, seed, timeout, max_iterations, dedup_key)
    """
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_PAYLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Payload exceeds maximum allowed size of {MAX_PAYLOAD_BYTES // (1024*1024)} MB",
                )
        except ValueError:
            pass

    content_type = request.headers.get("content-type", "")
    plan_id = None
    plan_data = None
    algorithm = "production_fallback"
    seed = 42
    timeout = 30.0
    max_iterations = 25

    if "multipart/form-data" in content_type:
        form = await request.form()
        uploaded_file = form.get("file")
        if uploaded_file and hasattr(uploaded_file, "read"):
            file_bytes = await uploaded_file.read()
            if len(file_bytes) > MAX_PAYLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Uploaded file exceeds maximum allowed size of {MAX_PAYLOAD_BYTES // (1024*1024)} MB",
                )
            try:
                plan_data = json.loads(file_bytes.decode("utf-8"))
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Malformed JSON in uploaded file: {exc}",
                )
        plan_id = form.get("plan_id")
        algorithm = str(form.get("algorithm", algorithm))
        try:
            seed = int(form.get("seed", seed))
        except (ValueError, TypeError):
            pass
        try:
            timeout = float(form.get("timeout", timeout))
        except (ValueError, TypeError):
            pass
        try:
            max_iterations = int(form.get("max_iterations", max_iterations))
        except (ValueError, TypeError):
            pass
    else:
        body = await request.body()
        if len(body) > MAX_PAYLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Request body exceeds maximum allowed size of {MAX_PAYLOAD_BYTES // (1024*1024)} MB",
            )
        if body:
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Malformed JSON in request body: {exc}",
                )
            if not isinstance(data, dict):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="JSON payload must be an object",
                )
            plan_id = data.get("plan_id")
            plan_data = data.get("plan_data") or data.get("plan")
            if not plan_id and not plan_data and ("rooms" in data or "coordinates" in data or "id" in data):
                plan_data = data
            algorithm = str(data.get("algorithm", algorithm))
            seed = int(data.get("seed", seed))
            timeout = float(data.get("timeout", timeout))
            max_iterations = int(data.get("max_iterations", max_iterations))

    dedup_key = None
    if plan_id is not None and str(plan_id).strip():
        plan_id_str = str(plan_id).strip()
        try:
            plan_input = resolve_metric_floor_plan(plan_id_str)
            dedup_key = f"plan_{plan_id_str}"
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to resolve floor plan '{plan_id_str}': {exc}",
            )
    elif plan_data is not None:
        is_val, err_msg, validated_dict = validate_floor_plan_json(plan_data)
        if not is_val or validated_dict is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err_msg or "Invalid floor plan JSON structure",
            )
        plan_input = validated_dict
        plan_id_str = str(validated_dict.get("id") or validated_dict.get("plan_id") or "uploaded_plan")
        raw_canonical = json.dumps(validated_dict, sort_keys=True)
        h = hashlib.sha256(raw_canonical.encode("utf-8")).hexdigest()[:16]
        dedup_key = f"custom_{h}"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'plan_id' or 'plan_data' must be supplied",
        )

    return plan_input, plan_id_str, algorithm, seed, timeout, max_iterations, dedup_key


async def parse_validation_request(
    request: Request,
) -> Tuple[Any, str, Dict[str, Any]]:
    """Parse validation request from JSON or multipart form, enforcing <= 10MB and safe JSON."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_PAYLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Payload exceeds maximum allowed size of {MAX_PAYLOAD_BYTES // (1024*1024)} MB",
                )
        except ValueError:
            pass

    body = await request.body()
    if len(body) > MAX_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Request body exceeds maximum allowed size of {MAX_PAYLOAD_BYTES // (1024*1024)} MB",
        )
    if not body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body cannot be empty",
        )
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed JSON: {exc}",
        )

    plan_id = data.get("plan_id")
    plan_data = data.get("plan_data") or data.get("plan")
    solution = data.get("solution")

    if solution is None and ("hubs" in data or "hub_rings" in data):
        solution = data
        if not plan_id and not plan_data:
            plan_id = data.get("plan_id")

    if solution is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'solution' payload to validate",
        )

    if plan_id is not None and str(plan_id).strip():
        plan_id_str = str(plan_id).strip()
        plan_input = resolve_metric_floor_plan(plan_id_str)
    elif plan_data is not None:
        is_val, err_msg, validated_dict = validate_floor_plan_json(plan_data)
        if not is_val or validated_dict is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err_msg or "Invalid floor plan JSON",
            )
        plan_input = adapt_resplan_record(validated_dict)
        plan_id_str = str(plan_input.plan_id)
    elif engine.plan:
        plan_input = engine.plan
        plan_id_str = str(engine.plan.plan_id)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Floor plan reference ('plan_id' or 'plan_data') is required for validation",
        )

    return plan_input, plan_id_str, solution


# ---------------------------------------------------------------------------
# Phase 18 Dataset Census Summary & Search Cache
# ---------------------------------------------------------------------------

_dataset_summary_cache: Optional[Dict[str, Any]] = None
_dataset_records_cache: Optional[List[Dict[str, Any]]] = None


def get_phase18_census_summary() -> Dict[str, Any]:
    global _dataset_summary_cache
    if _dataset_summary_cache is not None:
        return _dataset_summary_cache

    summary_path = Path("results/benchmarks/phase18/final_benchmark_summary.json")
    if not summary_path.exists():
        summary_path = Path(__file__).parents[3] / "results/benchmarks/phase18/final_benchmark_summary.json"

    integrity_path = Path("results/benchmarks/phase18/dataset_integrity_audit.json")
    if not integrity_path.exists():
        integrity_path = Path(__file__).parents[3] / "results/benchmarks/phase18/dataset_integrity_audit.json"

    summary_data: Dict[str, Any] = {}
    if summary_path.exists():
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                summary_data = json.load(f)
        except Exception as exc:
            logger.warning("Could not read %s: %s", summary_path, exc)

    integrity_data: Dict[str, Any] = {}
    if integrity_path.exists():
        try:
            with open(integrity_path, "r", encoding="utf-8") as f:
                integrity_data = json.load(f)
        except Exception as exc:
            logger.warning("Could not read %s: %s", integrity_path, exc)

    infeasibility_info = integrity_data.get("infeasibility_and_recovery_classification", {})

    census = {
        "status": "ok",
        "phase": 18,
        "certified_release_tag": summary_data.get("certified_release_tag", "v2.0-certified-production"),
        "title": summary_data.get("title", "Honeywell Cable Ring Optimization - Phase 18 Final Benchmark Summary"),
        "total_dataset_plans": 17000,
        "valid_plans": infeasibility_info.get("total_operational_verified_dataset", 16153),
        "infeasible_plans": infeasibility_info.get("cataloged_physical_infeasibilities", 22),
        "cadastral_structural_defects": infeasibility_info.get("cadastral_structural_defects_count", 181),
        "quarantined_plans": infeasibility_info.get("coarse_ingestion_quarantined_count", 644),
        "census_conservation_identity": infeasibility_info.get("census_conservation_identity", "17000 = 15506 + 647 + 22 + 181 + 644"),
        "validity_rate_pct": summary_data.get("validity_metrics", {}).get("overall_validity_rate_pct", 95.02),
        "routing_cost_j_stats": summary_data.get("routing_cost_j_stats", {}),
        "mean_cost_J": summary_data.get("routing_cost_j_stats", {}).get("mean", 60.0624),
        "hub_count_distribution": summary_data.get("hub_count_distribution", {}),
        "complexity_tier_breakdown": summary_data.get("complexity_tier_breakdown", {}),
        "failure_category_distribution": summary_data.get("failure_category_distribution", {}),
        "breakdown": {
            "operational_baseline_valid": infeasibility_info.get("operational_baseline_valid", 15506),
            "rescued_discrepancy_plans": infeasibility_info.get("genuinely_rescued_discrepancy_plans", 647),
            "inscribed_clearance_under_50cm": infeasibility_info.get("inscribed_clearance_under_50cm_count", 14),
            "isolated_degree_0_leaf": infeasibility_info.get("isolated_degree_0_leaf_room_count", 8),
        },
    }
    _dataset_summary_cache = census
    return _dataset_summary_cache


def get_dataset_records_index() -> List[Dict[str, Any]]:
    global _dataset_records_cache
    if _dataset_records_cache is not None:
        return _dataset_records_cache

    records: List[Dict[str, Any]] = []

    csv_candidates = [
        Path("results/benchmarks/benchmark_records_tall_full.csv"),
        Path(__file__).parents[3] / "results/benchmarks/benchmark_records_tall_full.csv",
    ]
    csv_path = next((p for p in csv_candidates if p.exists()), None)

    chk_candidates = [
        Path("results/benchmarks/phase18/checkpoint_phase18.json"),
        Path(__file__).parents[3] / "results/benchmarks/phase18/checkpoint_phase18.json",
    ]
    chk_path = next((p for p in chk_candidates if p.exists()), None)
    chk_records: Dict[str, Dict[str, Any]] = {}
    if chk_path:
        try:
            with open(chk_path, "r", encoding="utf-8") as f:
                chk_data = json.load(f)
                chk_records = chk_data.get("records", {})
        except Exception:
            pass

    if csv_path:
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pid_str = row.get("plan_id", "")
                    try:
                        pid = int(pid_str)
                    except ValueError:
                        continue
                    tier = row.get("tier", "unknown")
                    if tier == "unknown":
                        tier = "boundary"
                    is_val = row.get("is_valid", "").lower() in ("true", "1")
                    audit = chk_records.get(str(pid), {})
                    records.append({
                        "plan_id": pid,
                        "complexity": tier,
                        "tier": tier,
                        "status": audit.get("status") or row.get("solver_status", "OPTIMAL" if is_val else "INFEASIBLE"),
                        "is_valid": is_val,
                        "hub_count": int(row["h_count"]) if row.get("h_count") else audit.get("hub_count", 0),
                        "cost_J": float(row["total_cost_J"]) if row.get("total_cost_J") else audit.get("cost_J", 0.0),
                        "cable_length_m": float(row["length_m"]) if row.get("length_m") else audit.get("length_m", 0.0),
                        "crossings": int(row["crossings"]) if row.get("crossings") else audit.get("crossings", 0),
                        "overlap_m": float(row["overlap_m"]) if row.get("overlap_m") else audit.get("overlap_m", 0.0),
                        "bends": int(row["bends"]) if row.get("bends") else audit.get("bends", 0),
                        "runtime_s": float(row["runtime_s"]) if row.get("runtime_s") else audit.get("runtime_s", 0.0),
                        "eligible_rooms": int(row["eligible_rooms"]) if row.get("eligible_rooms") else 0,
                        "total_area_m2": float(row["total_area_m2"]) if row.get("total_area_m2") else 0.0,
                    })
        except Exception as exc:
            logger.warning("Error reading %s: %s", csv_path, exc)

    if not records and chk_records:
        for pid_str, r in chk_records.items():
            records.append({
                "plan_id": int(pid_str),
                "complexity": "standard",
                "tier": "standard",
                "status": r.get("status", "VALID_OPTIMIZED"),
                "is_valid": r.get("is_valid", True),
                "hub_count": r.get("hub_count", 0),
                "cost_J": r.get("cost_J", 0.0),
                "cable_length_m": r.get("length_m", 0.0),
                "crossings": r.get("crossings", 0),
                "overlap_m": r.get("overlap_m", 0.0),
                "bends": r.get("bends", 0),
                "runtime_s": r.get("runtime_s", 0.0),
                "eligible_rooms": 0,
                "total_area_m2": 0.0,
            })

    _dataset_records_cache = records
    return _dataset_records_cache


# ---------------------------------------------------------------------------
# Core Server REST & WebSocket Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check() -> Dict[str, str]:
    """Health check endpoint confirming engine availability."""
    return {
        "status": "ok",
        "engine": "IncrementalCoDesignEngine",
        "plan_id": engine.plan.plan_id if engine.plan else "none",
    }


@app.get("/api/state")
def get_current_state(include_bim: bool = False) -> Dict[str, Any]:
    """Fetch current floor plan state, metrics, halos, hubs, and routes."""
    return engine.get_state(include_bim=include_bim)


@app.get("/api/bim/scene")
def get_bim_scene_endpoint() -> Dict[str, Any]:
    """Fetch current 3D Building Information Modeling (BIM) scene for client-side Three.js rendering."""
    scene = engine.get_bim_scene()
    return scene.model_dump()


@app.get("/api/bim/export/obj")
def export_bim_obj_endpoint() -> Response:
    """Download formatted Wavefront .obj architectural model file."""
    obj_data = engine.export_obj()
    return Response(
        content=obj_data,
        media_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="resplan_model.obj"'},
    )


@app.get("/api/dataset/status")
def get_dataset_status() -> Dict[str, Any]:
    """Return status, readiness, and record count of bundled ResPlan dataset."""
    repo = DatasetRepository.get_instance()
    return repo.get_status()


@app.post("/api/dataset/plans/{plan_id}/load")
def load_dataset_plan_endpoint(plan_id: str) -> Any:
    """Load a specific Plan ID from the ResPlan dataset into unrouted preview mode."""
    try:
        state, latency_ms = engine.load_dataset_plan(plan_id)
        repo = DatasetRepository.get_instance()
        preview = repo.get_preview_geojson(plan_id)
        return {
            "status": "ok",
            "message": f"Dataset plan '{plan_id}' loaded into preview mode",
            "metadata": {
                "plan_id": preview["plan_id"],
                "total_area_m2": preview["total_area_m2"],
                "net_area_m2": preview["net_area_m2"],
                "eligible_rooms_count": preview["eligible_rooms_count"],
                "device_count": preview["device_count"],
            },
            "preview": preview,
            "state": state,
            "latency_ms": round(latency_ms, 2),
        }
    except PlanNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "error": str(exc), "message": str(exc)},
        )
    except (InvalidPlanIdError, MalformedRecordError, DatasetUnavailableError) as exc:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": str(exc), "message": str(exc)},
        )
    except Exception as exc:
        logger.exception("Failed to load dataset plan %s", plan_id)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(exc), "message": str(exc)},
        )


@app.post("/api/reset")
def reset_to_baseline() -> Dict[str, Any]:
    """Reset floor plan to baseline Slide 5 benchmark schematic."""
    state, _ = engine.reset()
    return state


@app.post("/api/upload")
async def upload_floor_plan(
    request: Request,
    file: Optional[UploadFile] = File(None),
) -> Any:
    """Upload floor plan JSON via file or JSON body, validate structure, and load preview."""
    # Check Content-Length limit <= 10MB
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_PAYLOAD_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"status": "error", "error": "PAYLOAD_TOO_LARGE", "message": f"Payload exceeds {MAX_PAYLOAD_BYTES // (1024*1024)} MB limit"},
                )
        except ValueError:
            pass

    payload_to_validate: Any = None
    if file is not None and file.filename:
        try:
            file_bytes = bytearray()
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                file_bytes.extend(chunk)
                if len(file_bytes) > MAX_PAYLOAD_BYTES:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"status": "error", "error": "PAYLOAD_TOO_LARGE", "message": "Uploaded file exceeds 10 MB limit"},
                    )
            payload_to_validate = bytes(file_bytes)
        except Exception as exc:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "error": f"Failed to read file: {exc}", "message": str(exc)},
            )
    else:
        try:
            body = await request.body()
            if len(body) > MAX_PAYLOAD_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"status": "error", "error": "PAYLOAD_TOO_LARGE", "message": "Request body exceeds 10 MB limit"},
                )
            payload_to_validate = body
        except Exception as exc:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "error": f"Failed to read request body: {exc}", "message": str(exc)},
            )

    if not payload_to_validate:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": "No file or JSON data uploaded", "message": "No file or JSON data uploaded"},
        )

    is_valid, err_msg, parsed_dict = validate_floor_plan_json(payload_to_validate)
    if not is_valid or parsed_dict is None:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": err_msg or "Invalid floor plan JSON", "message": err_msg},
        )

    try:
        state, _ = engine.load_plan_preview(parsed_dict)
        return {
            "status": "ok",
            "message": f"Floor plan '{engine.plan.plan_id}' loaded into preview mode",
            "state": state,
        }
    except Exception as exc:
        logger.exception("Failed to parse uploaded floor plan")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": str(exc), "message": str(exc)},
        )


@app.post("/api/generate")
def generate_cable_rings_endpoint() -> Any:
    """Run active floor plan through full routing pipeline and compute closed rings."""
    try:
        state, _ = engine.generate_cable_rings()
        return {
            "status": "ok",
            "message": "Cable rings generated successfully",
            "state": state,
        }
    except Exception as exc:
        logger.exception("Failed to generate cable rings")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": str(exc), "message": str(exc)},
        )


@app.post("/api/export")
def export_slide8_solution() -> Dict[str, Any]:
    """Export current floor plan routing solution to Slide 8 JSON format."""
    return engine.export_slide8()


@app.get("/api/export/json")
def download_slide8_json() -> Response:
    """Download official Slide 8 solution JSON file."""
    data = engine.export_slide8()
    plan_id = engine.plan.plan_id if engine.plan else "floor_plan"
    return Response(
        content=json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{plan_id}_solution.json"'},
    )


@app.get("/api/export/png")
def download_overlay_png() -> Response:
    """Download high-resolution Slide 5 schematic overlay PNG."""
    plan_id = engine.plan.plan_id if engine.plan else "floor_plan"
    if getattr(engine, "_preview_mode", False) or not engine.bundle or not engine.bundle.hub_rings:
        try:
            engine.generate_cable_rings()
        except Exception as exc:
            logger.warning("Could not auto-generate rings for PNG export: %s", exc)

    fig = FloorPlanVisualizer.render_overlay(
        plan=engine.plan,
        bundle=engine.bundle,
        evaluation=engine._last_report,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{plan_id}_overlay.png"'},
    )


@app.get("/api/demo/{demo_id}")
@app.post("/api/demo/{demo_id}")
def load_demo_endpoint(demo_id: str, generate: bool = False) -> Any:
    """Load one of the demo benchmark floor plans."""
    try:
        state, _ = engine.load_demo_plan(demo_id, generate=generate)
        return {
            "status": "ok",
            "message": f"Loaded demo plan '{demo_id}'",
            "state": state,
        }
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(exc)})


@app.post("/api/node/drag")
def drag_node_http(req: NodeDragRequest) -> Dict[str, Any]:
    """HTTP endpoint to move a device node."""
    state, _ = engine.update_node_position(req.node_id, req.x, req.y)
    return state


@app.post("/api/hub/drag")
def drag_hub_http(req: HubDragRequest) -> Dict[str, Any]:
    """HTTP endpoint to move a hub."""
    state, _ = engine.update_hub_position(req.hub_id, req.x, req.y)
    return state


@app.post("/api/copilot/suggest")
def copilot_suggest_http(req: Optional[SuggestRequest] = None) -> Any:
    """Return ranked floor-plan structural improvement proposals generated by Architectural Co-Pilot."""
    try:
        max_p = req.max_proposals if req is not None else 5
        report = engine.get_copilot_recommendations(max_proposals=max_p)
        return report.model_dump()
    except Exception as exc:
        logger.exception("Co-Pilot suggest failed")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": str(exc), "message": str(exc)},
        )


@app.post("/api/copilot/apply")
def copilot_apply_http(req: ProposalApplyRequest) -> Any:
    """Apply an architectural co-pilot recommendation and re-route floor plan."""
    try:
        state, _ = engine.apply_structural_proposal(req.proposal_id)
        return state
    except Exception as exc:
        logger.exception("Co-Pilot apply failed")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": str(exc), "message": str(exc)},
        )


@app.post("/api/survivability/cut")
def cut_wire_http(req: WireCutRequest) -> Dict[str, Any]:
    """Sever a cable leg and compute NFPA 72 Class A device survivability."""
    state, _ = engine.cut_wire(req.hub_id or "H1", req.cut_x, req.cut_y)
    if engine._active_cut is not None:
        return engine._active_cut.model_dump()
    return state


@app.post("/api/survivability/heal")
def heal_wire_http() -> Dict[str, Any]:
    """Restore severed wire cuts to normal closed loop."""
    state, _ = engine.heal_wire()
    return state


class MutationRequest(BaseModel):
    action: str
    plan_id: Optional[Union[str, int]] = None
    node_id: Optional[str] = None
    hub_id: Optional[str] = None
    door_id: Optional[str] = None
    room_id: Optional[str] = None
    proposal_id: Optional[str] = None
    demo_id: Optional[str] = None
    plan_data: Optional[Dict[str, Any]] = None
    x: Optional[float] = None
    y: Optional[float] = None
    offset_m: Optional[float] = 0.0
    delta_area_m2: Optional[float] = 0.0


@app.post("/api/mutate")
def mutate_http(req: MutationRequest) -> Any:
    """Execute programmatic floor plan mutation via HTTP REST."""
    action = req.action.lower()
    if action == "drag_node":
        if req.node_id is None or req.x is None or req.y is None:
            return JSONResponse(
                status_code=400,
                content={"error": "drag_node requires node_id, x, and y"},
            )
        state, _ = engine.update_node_position(req.node_id, req.x, req.y)
        return state

    elif action == "drag_hub":
        if req.hub_id is None or req.x is None or req.y is None:
            return JSONResponse(
                status_code=400,
                content={"error": "drag_hub requires hub_id, x, and y"},
            )
        state, _ = engine.update_hub_position(req.hub_id, req.x, req.y)
        return state

    elif action == "slide_door":
        if req.door_id is None:
            return JSONResponse(
                status_code=400,
                content={"error": "slide_door requires door_id"},
            )
        state, _ = engine.slide_door(req.door_id, req.offset_m or 0.0)
        return state

    elif action == "shift_wall":
        if req.room_id is None:
            return JSONResponse(
                status_code=400,
                content={"error": "shift_wall requires room_id"},
            )
        state, _ = engine.shift_room_boundary(req.room_id, req.delta_area_m2 or 0.0)
        return state

    elif action == "copilot_apply":
        if not req.proposal_id:
            return JSONResponse(
                status_code=400,
                content={"error": "copilot_apply requires proposal_id"},
            )
        state, _ = engine.apply_structural_proposal(req.proposal_id)
        return state

    elif action == "upload":
        if not req.plan_data:
            return JSONResponse(
                status_code=400,
                content={"error": "upload action requires plan_data"},
            )
        is_val, err, valid_dict = validate_floor_plan_json(req.plan_data)
        if not is_val or valid_dict is None:
            return JSONResponse(
                status_code=400,
                content={"error": err or "Invalid floor plan JSON"},
            )
        state, _ = engine.load_plan_preview(valid_dict)
        return state

    elif action == "generate_rings":
        state, _ = engine.generate_cable_rings()
        return state

    elif action == "load_demo":
        d_id = req.demo_id or "slide5"
        state, _ = engine.load_demo_plan(d_id, generate=True)
        return state

    elif action in ("load_plan_id", "load_dataset_plan"):
        target_pid = req.plan_id or req.demo_id
        if not target_pid:
            return JSONResponse(status_code=400, content={"error": "load_plan_id requires plan_id"})
        state, _ = engine.load_dataset_plan(target_pid)
        return state

    elif action == "reset":
        state, _ = engine.reset()
        return state

    elif action == "get_state":
        return engine.get_state()

    elif action == "wire_cut":
        cut_x = req.x if req.x is not None else 0.0
        cut_y = req.y if req.y is not None else 0.0
        state, _ = engine.cut_wire(req.hub_id or "H1", cut_x, cut_y)
        return state

    elif action == "wire_heal":
        state, _ = engine.heal_wire()
        return state

    else:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported mutation action '{req.action}'"},
        )


# ---------------------------------------------------------------------------
# Phase 20 Integration: Asynchronous Jobs, Direct Optimization & Details
# ---------------------------------------------------------------------------

@app.post("/api/jobs/optimize")
async def submit_optimization_job_endpoint(request: Request) -> Dict[str, Any]:
    """Submit asynchronous floor plan optimization job running in background worker pool.

    Accepts plan_id, uploaded JSON, algorithm, seed, timeout, max_iterations.
    Deduplicates redundant requests for active jobs.
    """
    plan_input, plan_id_str, algorithm, seed, timeout, max_iterations, dedup_key = await parse_optimization_request(request)

    job_info = job_manager.submit_job(
        plan_input=plan_input,
        plan_id=plan_id_str,
        algorithm=algorithm,
        seed=seed,
        timeout=timeout,
        max_iterations=max_iterations,
        dedup_key=dedup_key,
    )
    return job_info


@app.get("/api/jobs/{job_id}")
def get_job_status_endpoint(job_id: str) -> Dict[str, Any]:
    """Poll optimization job status, execution progress stage, elapsed time, and result payload."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Optimization job '{job_id}' not found",
        )
    return job


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job_endpoint(job_id: str) -> Dict[str, Any]:
    """Cancel a pending or active optimization job."""
    res = job_manager.cancel_job(job_id)
    if not res.get("found"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Optimization job '{job_id}' not found",
        )
    return res


@app.post("/api/optimize")
async def direct_optimize_endpoint(request: Request) -> Dict[str, Any]:
    """Direct synchronous floor plan optimization with timeout using resplan_router.api.optimize_plan."""
    plan_input, plan_id_str, algorithm, seed, timeout, max_iterations, _ = await parse_optimization_request(request)

    cfg = OptimizationConfig(
        algorithm=algorithm,
        random_seed=seed,
        time_limit_seconds=timeout,
        max_iterations=max_iterations,
        export_slide8=True,
    )
    result = optimize_plan(plan_input, config=cfg)
    return result.model_dump()


@app.get("/api/plans/{plan_id}/details")
def get_plan_details_endpoint(plan_id: str) -> Dict[str, Any]:
    """Return complete floor plan geometry (rooms, nodes, door portals, area, scale) for any valid plan."""
    metric_plan = resolve_metric_floor_plan(plan_id)

    rooms_list = []
    for rid, r in metric_plan.rooms.items():
        coords = []
        try:
            poly = shapely.wkt.loads(r.polygon_wkt)
            coords = [list(c) for c in poly.exterior.coords]
        except Exception:
            pass
        rooms_list.append({
            "room_id": str(rid),
            "category": getattr(r, "category", "room"),
            "is_eligible": bool(getattr(r, "is_eligible", True)),
            "area_m2": round(float(getattr(r, "area_m2", 0.0)), 4),
            "polygon_wkt": r.polygon_wkt,
            "coordinates": coords,
        })

    nodes_list = []
    for nid, n in metric_plan.nodes.items():
        loc = n.location if hasattr(n, "location") else {"x": n.x, "y": n.y}
        x = loc.x if hasattr(loc, "x") else loc.get("x", 0.0)
        y = loc.y if hasattr(loc, "y") else loc.get("y", 0.0)
        nodes_list.append({
            "node_id": str(nid),
            "room_id": str(getattr(n, "room_id", "")),
            "x": round(float(x), 4),
            "y": round(float(y), 4),
        })

    doors_list = []
    try:
        portals = extract_all_portals(metric_plan)
        for p in portals:
            doors_list.append({
                "portal_id": p.portal_id,
                "portal_type": str(getattr(p.portal_type, "value", p.portal_type)),
                "room1_id": str(p.room1_id),
                "room2_id": str(p.room2_id),
                "center": {"x": round(p.center_point.x, 4), "y": round(p.center_point.y, 4)},
                "width_m": round(p.width_m, 4),
                "threshold_wkt": p.threshold_wkt,
                "lane_a": {"x": round(p.lane_a_point.x, 4), "y": round(p.lane_a_point.y, 4)} if p.lane_a_point else None,
                "lane_b": {"x": round(p.lane_b_point.x, 4), "y": round(p.lane_b_point.y, 4)} if p.lane_b_point else None,
            })
    except Exception as exc:
        logger.warning("Portal extraction warning for plan %s: %s", plan_id, exc)

    obstacles_list = []
    if metric_plan.obstacles_wkt:
        try:
            obs = shapely.wkt.loads(metric_plan.obstacles_wkt)
            geoms = obs.geoms if hasattr(obs, "geoms") else [obs]
            for g in geoms:
                coords = [list(c) for c in g.exterior.coords] if hasattr(g, "exterior") else []
                obstacles_list.append({"wkt": g.wkt, "coordinates": coords})
        except Exception:
            pass

    return {
        "status": "ok",
        "plan_id": str(metric_plan.plan_id),
        "total_area_m2": round(float(metric_plan.total_area_m2), 4),
        "scale_units_per_m": round(float(metric_plan.scale_units_per_m), 6),
        "rooms_count": len(rooms_list),
        "nodes_count": len(nodes_list),
        "doors_count": len(doors_list),
        "rooms": rooms_list,
        "nodes": nodes_list,
        "doors": doors_list,
        "obstacles": obstacles_list,
    }


@app.get("/api/dataset/summary")
def get_dataset_summary_endpoint() -> Dict[str, Any]:
    """Return pre-aggregated Phase 18 reconciled census statistics loaded lazily."""
    return get_phase18_census_summary()


@app.get("/api/dataset/plans")
def search_dataset_plans_endpoint(
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(default=None, description="Search plan_id"),
    status: Optional[str] = Query(default=None, description="Filter by status (e.g. VALID_OPTIMIZED, INFEASIBLE)"),
    complexity: Optional[str] = Query(default=None, description="Filter by complexity tier (compact, standard, complex, boundary)"),
) -> Dict[str, Any]:
    """Search and browse dataset plan benchmark records without reloading heavy geometry into memory."""
    all_records = get_dataset_records_index()

    filtered = all_records
    if search:
        s_term = search.strip().lower()
        filtered = [r for r in filtered if s_term in str(r["plan_id"]).lower()]

    if status:
        st_term = status.strip().lower()
        if st_term in ("valid", "true"):
            filtered = [r for r in filtered if r.get("is_valid") is True]
        elif st_term in ("invalid", "false", "infeasible"):
            filtered = [r for r in filtered if not r.get("is_valid") or "infeasible" in str(r.get("status", "")).lower()]
        else:
            filtered = [r for r in filtered if st_term in str(r.get("status", "")).lower()]

    if complexity:
        c_term = complexity.strip().lower()
        filtered = [r for r in filtered if c_term == str(r.get("complexity", "")).lower() or c_term == str(r.get("tier", "")).lower()]

    total = len(filtered)
    pages = math.ceil(total / limit) if total > 0 else 1
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    page_records = filtered[start_idx:end_idx]

    return {
        "status": "ok",
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
        "records": page_records,
    }


@app.post("/api/validate")
async def validate_solution_dual_oracle(request: Request) -> Dict[str, Any]:
    """Execute dual-oracle validation running FloorPlanChecker and IndependentVerifier on uploaded solutions."""
    plan, plan_id_str, solution = await parse_validation_request(request)

    dual_res = IndependentVerifier.dual_validate(plan=plan, raw_submission=solution, tolerance_m=0.001)

    return {
        "status": "VALID" if dual_res.official_valid else "INVALID",
        "plan_id": plan_id_str,
        "is_valid": dual_res.official_valid,
        "concordant": dual_res.is_concordant,
        "dual_oracle": {
            "is_concordant": dual_res.is_concordant,
            "official_valid": dual_res.official_valid,
            "independent_valid": dual_res.independent_valid,
            "rules_concordant": dual_res.rules_concordant,
            "metrics_concordant": dual_res.metrics_concordant,
            "delta_J": round(dual_res.delta_J, 4),
            "delta_L": round(dual_res.delta_L, 4),
            "delta_X": dual_res.delta_X,
            "delta_O": round(dual_res.delta_O, 4),
            "delta_B": dual_res.delta_B,
            "delta_area_gap": round(dual_res.delta_area_gap, 4),
            "discrepancies": dual_res.discrepancies,
        },
        "official_report": dual_res.official_report.model_dump(),
        "independent_report": dual_res.independent_report.model_dump(),
    }


# ---------------------------------------------------------------------------
# Mount Export & Comparison Routers
# ---------------------------------------------------------------------------

app.include_router(create_export_router(engine))


@app.post("/api/comparison/csv")
async def top_level_comparison_csv(request: Request) -> Response:
    """Export comparison runs in CSV format."""
    body = await request.json()
    runs = body if isinstance(body, list) else body.get("runs", [])
    csv_str = ExportService.export_comparison_csv(runs)
    return Response(
        content=csv_str,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="resplan_comparison.csv"'},
    )


@app.post("/api/comparison/json")
async def top_level_comparison_json(request: Request) -> Response:
    """Export comparison runs in JSON format."""
    body = await request.json()
    runs = body if isinstance(body, list) else body.get("runs", [])
    json_str = ExportService.export_comparison_json(runs)
    return Response(
        content=json_str,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="resplan_comparison.json"'},
    )


# ---------------------------------------------------------------------------
# WebSocket Real-Time Co-Design Channel
# ---------------------------------------------------------------------------

@app.websocket("/ws/co-design")
async def websocket_co_design(websocket: WebSocket) -> None:
    """Sub-50 ms bidirectional WebSocket channel for live interactive co-design canvas."""
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action", "")

            if action == "drag_node":
                node_id = str(data.get("node_id", ""))
                x = float(data.get("x", 0.0))
                y = float(data.get("y", 0.0))
                state, _ = engine.update_node_position(node_id, x, y)
                await websocket.send_json(state)

            elif action == "drag_hub":
                hub_id = str(data.get("hub_id", ""))
                x = float(data.get("x", 0.0))
                y = float(data.get("y", 0.0))
                state, _ = engine.update_hub_position(hub_id, x, y)
                await websocket.send_json(state)

            elif action == "slide_door":
                door_id = str(data.get("door_id", ""))
                offset_m = float(data.get("offset_m", 0.0))
                state, _ = engine.slide_door(door_id, offset_m)
                await websocket.send_json(state)

            elif action == "shift_wall":
                room_id = str(data.get("room_id", ""))
                delta_area_m2 = float(data.get("delta_area_m2", 0.0))
                state, _ = engine.shift_room_boundary(room_id, delta_area_m2)
                await websocket.send_json(state)

            elif action == "copilot_suggest":
                max_proposals = int(data.get("max_proposals", 5))
                try:
                    report = engine.get_copilot_recommendations(max_proposals=max_proposals)
                    await websocket.send_json({
                        "type": "copilot_proposals",
                        "report": report.model_dump(),
                    })
                except Exception as exc:
                    logger.exception("WebSocket copilot_suggest failed")
                    await websocket.send_json({
                        "type": "copilot_error",
                        "error": str(exc),
                        "message": str(exc),
                    })

            elif action == "copilot_apply":
                proposal_id = str(data.get("proposal_id", ""))
                try:
                    state, _ = engine.apply_structural_proposal(proposal_id)
                    await websocket.send_json(state)
                except Exception as exc:
                    logger.exception("WebSocket copilot_apply failed")
                    await websocket.send_json({
                        "type": "copilot_error",
                        "error": str(exc),
                        "message": str(exc),
                    })

            elif action == "reset":
                state, _ = engine.reset()
                await websocket.send_json(state)

            elif action == "get_state":
                include_bim = bool(data.get("include_bim", False))
                state = engine.get_state(include_bim=include_bim)
                await websocket.send_json(state)

            elif action == "get_bim_scene":
                scene = engine.get_bim_scene()
                await websocket.send_json({
                    "type": "bim_scene_update",
                    "scene": scene.model_dump(),
                })

            elif action == "wire_cut":
                hub_id = str(data.get("hub_id", ""))
                cut_x = float(data.get("cut_x", data.get("x", 0.0)))
                cut_y = float(data.get("cut_y", data.get("y", 0.0)))
                state, _ = engine.cut_wire(hub_id, cut_x, cut_y)
                report_dict = engine._active_cut.model_dump() if engine._active_cut else {}
                resp = dict(state)
                resp["type"] = "survivability_update"
                resp["report"] = report_dict
                await websocket.send_json(resp)

            elif action == "upload":
                plan_data = data.get("plan_data") or data.get("plan")
                if not plan_data:
                    await websocket.send_json({"type": "error", "message": "upload action requires plan_data"})
                else:
                    is_val, err, valid_dict = validate_floor_plan_json(plan_data)
                    if not is_val or valid_dict is None:
                        await websocket.send_json({"type": "error", "message": err or "Invalid floor plan JSON"})
                    else:
                        state, _ = engine.load_plan_preview(valid_dict)
                        await websocket.send_json(state)

            elif action == "generate_rings":
                try:
                    state, _ = engine.generate_cable_rings()
                    await websocket.send_json(state)
                except Exception as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})

            elif action == "load_demo":
                demo_id = str(data.get("demo_id", "slide5"))
                gen = bool(data.get("generate", False))
                try:
                    state, _ = engine.load_demo_plan(demo_id, generate=gen)
                    await websocket.send_json(state)
                except Exception as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})

            elif action in ("load_plan_id", "load_dataset_plan"):
                plan_id = data.get("plan_id")
                try:
                    state, _ = engine.load_dataset_plan(plan_id)
                    await websocket.send_json(state)
                except Exception as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})

            elif action == "wire_heal":
                state, _ = engine.heal_wire()
                resp = dict(state)
                resp["type"] = "state_update"
                await websocket.send_json(resp)

            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unsupported co-design action '{action}'",
                })

    except WebSocketDisconnect:
        logger.info("Client disconnected from /ws/co-design")
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)


# Mount static assets directory if available (for Wave 2 studio frontend)
static_dir = Path(__file__).parent / "static"
if static_dir.exists() and (static_dir / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
