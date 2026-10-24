"""Unit Test Suite for Configuration, Hardware Telemetry, Reproducibility Manifests, and Cache Versioning.

Phase 19 Agent 3 Unit Tests (C:\\hackhive\\tests\\unit\\test_reproducibility.py).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import pytest

from resplan_router.reproducibility import (
    CACHE_SCHEMA_VERSION,
    CANONICAL_OBJECTIVE_WEIGHTS,
    DEFAULT_BASE_SEED,
    DEFAULT_DATASET_PATH,
    EXPECTED_DATASET_SHA256,
    LEXICOGRAPHICAL_HIERARCHY,
    MANIFEST_SCHEMA_VERSION,
    CacheConfiguration,
    DatasetMetadata,
    DependencyMetadata,
    GitMetadata,
    HardwareMetadata,
    ManifestValidationReport,
    RunManifest,
    SafeVersionedCacheStore,
    SeedPolicy,
    SolverConfiguration,
    atomic_save_manifest,
    calculate_deterministic_seed,
    compute_cache_key,
    compute_file_sha256,
    configure_worker_determinism,
    extract_dependency_metadata,
    extract_environment_metadata,
    extract_git_metadata,
    extract_hardware_metadata,
    generate_run_manifest,
    validate_run_manifest,
    verify_dataset_checksum,
)


# =============================================================================
# 1. METADATA EXTRACTION TESTS
# =============================================================================

class TestMetadataExtraction:
    """Validate extraction of Git, hardware, and environment dependencies."""

    def test_extract_git_metadata(self) -> None:
        meta = extract_git_metadata()
        assert isinstance(meta, GitMetadata)
        assert len(meta.commit_full) == 40
        assert all(c in "0123456789abcdef" for c in meta.commit_full.lower())
        assert 4 <= len(meta.commit_short) <= 12
        assert meta.commit_full.startswith(meta.commit_short)
        assert isinstance(meta.branch, str) and len(meta.branch) > 0
        assert isinstance(meta.is_clean, bool)
        assert isinstance(meta.tags, list)

    def test_extract_hardware_metadata(self) -> None:
        hw = extract_hardware_metadata()
        assert isinstance(hw, HardwareMetadata)
        assert hw.cpu_count_logical >= 1
        assert hw.cpu_count_physical >= 1
        assert hw.cpu_count_logical >= hw.cpu_count_physical
        assert hw.total_ram_bytes > 0
        assert hw.total_ram_gb > 0.0
        assert hw.available_ram_bytes >= 0
        assert hw.available_ram_gb >= 0.0
        assert len(hw.platform) > 0
        assert len(hw.os_name) > 0

    def test_extract_dependency_metadata(self) -> None:
        deps = extract_dependency_metadata()
        assert isinstance(deps, DependencyMetadata)
        assert deps.python_version.startswith("3.")
        assert deps.python_implementation in {"CPython", "PyPy"}
        assert deps.numpy_version is not None
        assert deps.shapely_version is not None
        assert deps.pydantic_version is not None

    def test_extract_environment_metadata_convenience(self) -> None:
        hw, deps = extract_environment_metadata()
        assert isinstance(hw, HardwareMetadata)
        assert isinstance(deps, DependencyMetadata)


# =============================================================================
# 2. CRYPTOGRAPHIC DATASET PROVENANCE TESTS
# =============================================================================

class TestDatasetChecksumProvenance:
    """Validate streaming SHA-256 checksums and dataset integrity checks."""

    def test_streaming_sha256_synthetic(self, tmp_path: Path) -> None:
        test_file = tmp_path / "synthetic_payload.bin"
        payload = b"Honeywell ResPlan Cable Ring Optimization 2026 Test Payload" * 1000
        test_file.write_bytes(payload)

        expected_hash = hashlib.sha256(payload).hexdigest().lower()
        computed_hash = compute_file_sha256(test_file, chunk_size=512)
        assert computed_hash == expected_hash

    def test_streaming_sha256_missing_file_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "non_existent_file.bin"
        with pytest.raises(FileNotFoundError):
            compute_file_sha256(missing)

    def test_verify_dataset_checksum_synthetic(self, tmp_path: Path) -> None:
        test_file = tmp_path / "data.bin"
        payload = b"Exact deterministic data"
        test_file.write_bytes(payload)
        expected_hash = hashlib.sha256(payload).hexdigest().lower()

        assert verify_dataset_checksum(test_file, expected_hash) is True
        assert verify_dataset_checksum(test_file, "0000000000000000000000000000000000000000000000000000000000000000") is False

    def test_canonical_dataset_file_if_present(self) -> None:
        if DEFAULT_DATASET_PATH.is_file():
            # Checksum matches official standard
            assert verify_dataset_checksum(DEFAULT_DATASET_PATH, EXPECTED_DATASET_SHA256) is True


# =============================================================================
# 3. DETERMINISTIC SEED POLICY & PRNG ISOLATION TESTS
# =============================================================================

class TestDeterministicSeedPolicy:
    """Validate mathematical seed determinism and PRNG isolation across workers."""

    def test_seed_formula_integer_plans(self) -> None:
        base = DEFAULT_BASE_SEED
        assert calculate_deterministic_seed(0, base_seed=base) == 42
        assert calculate_deterministic_seed(1, base_seed=base) == 43
        assert calculate_deterministic_seed(64, base_seed=base) == 106
        assert calculate_deterministic_seed(16999, base_seed=base) == 17041

    def test_seed_formula_string_plans(self) -> None:
        base = DEFAULT_BASE_SEED
        # Numeric string matches integer
        assert calculate_deterministic_seed("64", base_seed=base) == 106
        assert calculate_deterministic_seed("0", base_seed=base) == 42

        # Non-numeric string produces deterministic positive 31-bit seed
        s1 = calculate_deterministic_seed("TEST_PLAN_ALPHA", base_seed=base)
        s2 = calculate_deterministic_seed("TEST_PLAN_ALPHA", base_seed=base)
        assert s1 == s2
        assert 0 <= s1 < 2147483647

    def test_prng_worker_isolation_identical_seeds(self) -> None:
        seed = calculate_deterministic_seed(64)
        rng1 = configure_worker_determinism(seed)
        sample1 = rng1.uniform(0.0, 1.0, size=10)

        rng2 = configure_worker_determinism(seed)
        sample2 = rng2.uniform(0.0, 1.0, size=10)

        # Bitwise identical floating point arrays
        assert list(sample1) == list(sample2)

    def test_prng_worker_isolation_different_seeds(self) -> None:
        rng1 = configure_worker_determinism(calculate_deterministic_seed(10))
        sample1 = rng1.uniform(0.0, 1.0, size=10)

        rng2 = configure_worker_determinism(calculate_deterministic_seed(20))
        sample2 = rng2.uniform(0.0, 1.0, size=10)

        assert list(sample1) != list(sample2)


# =============================================================================
# 4. VERSIONED SOLVER CONFIGURATION TESTS
# =============================================================================

class TestSolverConfiguration:
    """Validate solver configuration versioning and hashing."""

    def test_default_solver_configuration_tolerances_and_rules(self) -> None:
        cfg = SolverConfiguration()
        assert cfg.solver_version == "2.0.0"
        assert cfg.spatial_tolerance_m == 0.001
        assert cfg.hub_obstacle_clearance_m == 0.50
        assert cfg.wall_clearance_m == 0.50
        assert cfg.max_nodes_per_hub == 5
        assert cfg.max_area_m2 == 100.0
        assert cfg.weights == CANONICAL_OBJECTIVE_WEIGHTS
        assert cfg.lexicographical_hierarchy == LEXICOGRAPHICAL_HIERARCHY

    def test_compute_config_hash_deterministic(self) -> None:
        cfg1 = SolverConfiguration()
        cfg2 = SolverConfiguration()
        assert cfg1.compute_config_hash() == cfg2.compute_config_hash()

        cfg3 = SolverConfiguration(max_iterations=100)
        assert cfg1.compute_config_hash() != cfg3.compute_config_hash()


# =============================================================================
# 5. CACHE SCHEMA VERSIONING & SAFE EVICTION TESTS
# =============================================================================

class TestSafeVersionedCacheStore:
    """Validate cache key precision, version enforcement, and tagged invalidations."""

    def test_compute_cache_key_millimeter_precision(self) -> None:
        key1 = compute_cache_key(
            plan_id="64",
            u_id="H0",
            v_id="N1",
            u_coord=(5.123456, 10.987654),
            v_coord=(8.0, 12.0),
        )
        key2 = compute_cache_key(
            plan_id="64",
            u_id="H0",
            v_id="N1",
            u_coord=(5.1235, 10.9877),  # Exactly rounded to 4 decimals
            v_coord=(8.0000, 12.0000),
        )
        assert key1 == key2
        assert "v2.0.0:64:H0@5.1235,10.9877->N1@8.0000,12.0000" in key1

    def test_cache_store_basic_put_get(self) -> None:
        store = SafeVersionedCacheStore(schema_version="2.0.0", maxsize=10)
        key = compute_cache_key("64", "H0", "N1", (1.0, 1.0), (2.0, 2.0))
        store.put(key, value={"cost_J": 12.5}, plan_id="64", tags=["hub:H0"])

        val = store.get(key)
        assert val is not None
        assert val["cost_J"] == 12.5

        stats = store.get_telemetry()
        assert stats["hits"] == 1
        assert stats["misses"] == 0
        assert stats["size"] == 1

    def test_cache_store_lru_eviction(self) -> None:
        store = SafeVersionedCacheStore(schema_version="2.0.0", maxsize=3)
        for i in range(5):
            store.put(f"key_{i}", f"val_{i}")

        assert len(store) == 3
        assert store.get("key_0") is None  # Evicted
        assert store.get("key_1") is None  # Evicted
        assert store.get("key_4") == "val_4"
        stats = store.get_telemetry()
        assert stats["evictions"] == 2

    def test_cache_store_tag_invalidation_hub_relocation(self) -> None:
        store = SafeVersionedCacheStore(schema_version="2.0.0", maxsize=50)
        # Store 2 routes involving H0, 1 involving H1
        k_h0_n1 = compute_cache_key("64", "H0", "N1", (5.0, 5.0), (10.0, 5.0))
        k_h0_n2 = compute_cache_key("64", "H0", "N2", (5.0, 5.0), (12.0, 8.0))
        k_h1_n3 = compute_cache_key("64", "H1", "N3", (20.0, 20.0), (22.0, 25.0))

        store.put(k_h0_n1, "route_h0_n1", plan_id="64", tags=["hub:H0"])
        store.put(k_h0_n2, "route_h0_n2", plan_id="64", tags=["hub:H0"])
        store.put(k_h1_n3, "route_h1_n3", plan_id="64", tags=["hub:H1"])

        assert len(store) == 3

        # Invalidate H0 (hub relocated)
        evicted = store.invalidate_hub("H0")
        assert evicted == 2
        assert len(store) == 1
        assert store.get(k_h0_n1) is None
        assert store.get(k_h0_n2) is None
        assert store.get(k_h1_n3) == "route_h1_n3"

    def test_cache_store_plan_invalidation(self) -> None:
        store = SafeVersionedCacheStore(schema_version="2.0.0", maxsize=50)
        k1 = compute_cache_key("64", "H0", "N1", (1.0, 1.0), (2.0, 2.0))
        k2 = compute_cache_key("65", "H0", "N1", (1.0, 1.0), (2.0, 2.0))

        store.put(k1, "val_64", plan_id="64")
        store.put(k2, "val_65", plan_id="65")

        store.invalidate_plan("64")
        assert store.get(k1) is None
        assert store.get(k2) == "val_65"

    def test_cache_store_schema_version_mismatch_eviction(self) -> None:
        store = SafeVersionedCacheStore(schema_version="2.0.0", maxsize=10)
        key = "legacy_entry"
        # Insert entry with older schema version "1.0.0"
        store.put(key, "legacy_val", schema_version="1.0.0")

        # Querying with active schema_version "2.0.0" triggers version mismatch eviction
        retrieved = store.get(key)
        assert retrieved is None

        telemetry = store.get_telemetry()
        assert telemetry["version_mismatches"] == 1
        assert telemetry["stale_prevented"] == 1
        assert len(store) == 0

    def test_cache_store_upgrade_schema_version(self) -> None:
        store = SafeVersionedCacheStore(schema_version="2.0.0", maxsize=10)
        store.put("k1", "val1", schema_version="2.0.0")
        store.put("k2", "val2", schema_version="2.0.0")

        # Upgrade store to 2.1.0
        purged = store.upgrade_schema_version("2.1.0")
        assert purged == 2
        assert len(store) == 0
        assert store.schema_version == "2.1.0"

    def test_cache_store_ttl_expiration(self) -> None:
        store = SafeVersionedCacheStore(schema_version="2.0.0", maxsize=10)
        store.put("quick_expire", "val")
        time.sleep(0.05)
        # Evict older than 0.02s
        evicted = store.evict_expired(ttl_seconds=0.02)
        assert evicted == 1
        assert len(store) == 0


# =============================================================================
# 6. RUN MANIFEST GENERATION & VALIDATION TESTS
# =============================================================================

class TestRunManifestWorkflow:
    """Validate generation, atomic serialization, and auditing of run manifests."""

    def test_generate_run_manifest_in_memory(self) -> None:
        manifest = generate_run_manifest(verify_dataset_hash=False)
        assert isinstance(manifest, RunManifest)
        assert manifest.phase == 19
        assert manifest.manifest_version == "2.0.0"
        assert manifest.certified_release_tag == "v2.0-full-production-certified"
        assert len(manifest.git_metadata.commit_full) == 40
        assert manifest.solver_architecture.max_nodes_per_hub == 5
        assert manifest.seed_policy.base_seed == 42

    def test_atomic_save_and_reload_manifest(self, tmp_path: Path) -> None:
        manifest = generate_run_manifest(verify_dataset_hash=False)
        target_path = tmp_path / "subfolder" / "test_manifest.json"

        saved_path = atomic_save_manifest(manifest, target_path)
        assert saved_path.is_file()

        with open(saved_path, "r", encoding="utf-8") as f:
            loaded_json = json.load(f)

        assert loaded_json["manifest_version"] == "2.0.0"
        assert loaded_json["phase"] == 19

    def test_validate_valid_manifest(self) -> None:
        manifest = generate_run_manifest(verify_dataset_hash=False)
        report = validate_run_manifest(manifest, verify_dataset_hash=False)
        assert isinstance(report, ManifestValidationReport)
        assert report.is_valid is True
        assert len(report.errors) == 0

    def test_validate_corrupted_manifest_phase_mismatch(self) -> None:
        manifest = generate_run_manifest(verify_dataset_hash=False)
        data = manifest.model_dump()
        data["phase"] = 17  # Tampered phase

        report = validate_run_manifest(data, expected_phase=19)
        assert report.is_valid is False
        assert any("Phase mismatch" in e for e in report.errors)

    def test_validate_corrupted_manifest_invalid_git_sha(self) -> None:
        manifest = generate_run_manifest(verify_dataset_hash=False)
        data = manifest.model_dump()
        data["git_metadata"]["commit_full"] = "invalid_non_hex_sha_value"

        report = validate_run_manifest(data)
        assert report.is_valid is False
        assert any("Git commit SHA" in e or "validation error" in e for e in report.errors)

    def test_validate_corrupted_manifest_tampered_weights(self) -> None:
        manifest = generate_run_manifest(verify_dataset_hash=False)
        data = manifest.model_dump()
        data["solver_architecture"]["weights"]["weight_crossing"] = 1.0  # Tampered weight (should be 5.0)

        report = validate_run_manifest(data)
        assert report.is_valid is False
        assert any("cost weights violated" in e for e in report.errors)

    def test_validate_corrupted_manifest_dataset_checksum_mismatch(self) -> None:
        manifest = generate_run_manifest(verify_dataset_hash=False)
        data = manifest.model_dump()
        data["dataset"]["sha256"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

        report = validate_run_manifest(data)
        assert report.is_valid is False
        assert any("Dataset SHA-256 mismatch" in e for e in report.errors)
