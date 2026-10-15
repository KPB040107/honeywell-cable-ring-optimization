# SPEC.md — Project Specification: resplan-cable-opt-v1

> **Status**: `FINALIZED`
> **Project Code**: `resplan-cable-opt-v1`
> **Benchmark**: Honeywell Technologies / UR Hackathon 2026H2 Problem Statement 4 (PS4)
> **Authoritative Baseline**: `prd_v1_control_panel_cable_routing_foundation.md`

---

## 1. Vision & System Overview

`resplan-cable-opt-v1` delivers a deterministic, mathematically verified, reproducible Python 3.12 algorithmic and backend system for automated residential electrical/control cabling. Given a residential floor plan representation (starting with canonical benchmark fixtures and Kaggle ResPlan-compatible JSON schemas), the system jointly solves:

1. **Hub Count & Placement**: Minimizes the number of control panels $K$ (subject to $K \ge K_{\min}$) and selects exact physical coordinates inside eligible rooms, maintaining clear spatial offsets from device nodes and doorways.
2. **Contiguous Zone Assignment**: Assigns every eligible room (`living`, `bedroom`, `kitchen`, `storage`) and its single fixed node to a hub such that each hub's assigned rooms form a single connected territory linked exclusively through doors or open passages, strictly respecting area ($\le 100\text{ m}^2$) and device count ($\le N_{\max}$, default 5, configurable to 20) capacity constraints. Non-eligible transit spaces (corridors) contribute 0 devices and 0 area load but provide valid transit connectivity.
3. **Closed-Ring Device Sequencing (TSP)**: Determines the visitation sequence $H_k \to N_{k,1} \to \dots \to N_{k,m} \to H_k$ for each panel's closed, non-branching ring. Default $m \le 5$ (and up to $m \le 12$) is solved to exact global optimality via Held-Karp bitmask dynamic programming.
4. **Obstacle-Aware Axis-Aligned Physical Routing**: Synthesizes horizontal and vertical ($\Delta x = 0$ or $\Delta y = 0$) rectilinear cable paths staying strictly inside interior boundaries, avoiding walls and windows, passing between rooms exclusively through doors and open passages, and utilizing dual-lane portal offsets ($2\delta$) in single-door leaf rooms to prevent collinear overlap or self-intersection.
5. **Multi-Criteria Lexicographic Optimization**: Primary objective: minimize hub count $K$. Secondary objective: minimize total installation cost $J = \sum_{k} (w_{\text{len}} L_k + w_{\text{bend}} B_k + w_{\text{ovlp}} O_k) + w_{\text{cross}} X$.
6. **Validation & Visual Export**: Performs independent programmatic audit of all 8 hard constraints via `DesignValidator`, exports deterministic scoring JSON, and renders high-resolution schematic dual-panel overlays (PNG + SVG) matching competition presentation standards.

---

## 2. Core Goals

1. **Mathematical Correctness & Constraint Satisfaction**: 100% compliance with all 8 hard competition rules; zero tolerance for wall intersections, disconnected zones, open cable rings, capacity violations, or diagonal segments.
2. **Reproducibility & Determinism**: Given identical input geometry and configuration, output identical hub locations, zone partitions, ring sequences, waypoints, and cost metrics across runs and platforms.
3. **Exact vs. Heuristic Transparency**: Exact methods for lower bounds, zone partitioning (CP-SAT MILP), micro-routing ($A^*$), and TSP ($m \le 12$). Transparent documentation of approximations (proxy costs, CCVT fallback, OR-Tools metaheuristics for $m > 12$).
4. **Clean Modular Architecture**: Clean separation across 6 pipeline phases mediated strictly by immutable Pydantic contracts.
5. **Comprehensive Automated Verification**: Unit tests, invariant checks, adversarial/negative mutation tests, and regression gates across all phases.

---

## 3. Non-Goals (Out of Scope for V1)

- No Web frontend, React dashboard, WebGL, or interactive UI sliders (strictly CLI + JSON + Matplotlib PNG/SVG in V1).
- No 3D BIM, conduit bend radius physics, or multi-floor vertical riser routing (pure 2D planar floor plan analysis).
- No speculative novelties or post-V1 ML models before all 6 foundation phases pass 100% of verification gates.
- No global optimality claims for the end-to-end joint combinatorial space where decomposition heuristics are applied.

---

## 4. Users & Operational Context

- **Primary Users**: Competition evaluators, algorithmic researchers, and electrical systems automation engineers assessing solution optimality, constraint compliance, and visual clarity against PS4 standards.
- **Execution Mode**: CLI tool (`python -m resplan_router.cli --config configs/strict_v1.yaml --input <plan.json> --output-dir <dist/>`) and importable Python library.

---

## 5. Constraints & Environment

- **Python Runtime**: Python 3.12+ (isolated virtual environment in `.venv`).
- **Core Dependencies**:
  - `pydantic >= 2.0` (immutable contracts and serialization)
  - `shapely >= 2.0` (GEOS C-backend computational geometry, polylabel, spatial indexing)
  - `networkx >= 3.0` (macro topology, SCF flow formulation, graph connectivity)
  - `numpy >= 1.24`, `scipy >= 1.10` (spatial metrics, coordinate snapping)
  - `ortools >= 9.8` (CP-SAT MILP for zone partitioning, RoutingModel for TSP $m > 12$)
  - `matplotlib >= 3.7` (publication-grade PNG/SVG schematic overlays)
  - `pytest >= 7.0` (automated testing and verification gates)
- **Performance Budget**:
  - Ingestion & validation: $< 150\text{ ms}$
  - Topology extraction: $< 100\text{ ms}$
  - Micro-routing index: $< 800\text{ ms}$
  - Zone partition MILP: $< 1500\text{ ms}$
  - Ring synthesis & TSP: $< 500\text{ ms}$
  - Overall pipeline: $< 3.5\text{ s}$ per standard residential plan.

---

## 6. Success & Acceptance Criteria

- [ ] Canonical synthetic 6-room benchmark fixture (`schematic_6room.json`) constructed with exact $118\text{ m}^2$ area, matching Slide 5/8 layout and dimensions.
- [ ] Documented schema-validated importer for real ResPlan dataset samples.
- [ ] $K = 2$ achieved on the 6-room benchmark, matching $K_{\min} = \max(\lceil 118/100 \rceil, \lceil 6/5 \rceil) = 2$.
- [ ] 100% passing rate on the 8 independent verification rules in `DesignValidator`.
- [ ] Adversarial mutation test suite catching simulated violations (wall crosses, open loops, wrong hub return, diagonal segments, overloaded areas).
- [ ] Dual-panel schematic visualization exported as PNG and SVG matching Slide 5/8 presentation format.
- [ ] Deterministic scoring JSON serialized with zero discrepancy in metrics ($L, B, O, X, J$).
