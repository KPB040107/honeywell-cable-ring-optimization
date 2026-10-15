# DECISIONS.md — Architecture Decision Records

> **Purpose**: Log significant technical decisions, scope partitions, and their rationale.

---

## [DEC-001] Two-Milestone Project Partition (V1 Foundation vs. V2 Interactive Studio)

**Date**: 2026-10-15  
**Status**: Accepted  

### Context
`v2.md` defines both a core algorithmic/backend optimization engine (Phases 1–6) and a 60 FPS real-time co-design interactive studio with 3D WebGL (Phases 7–10). A clean milestone partition is needed to ensure the core mathematical and checker pipeline is rock-solid and empirically verified before layering complex web/UI dependencies.

### Decision
Treat Phases 1–6 as Milestone 1 ("V1 Foundation") and Phases 7–10 as Milestone 2 ("V2 Interactive Co-Design Extension"). Execution begins exclusively on V1.

### Rationale
Enables rigorous compliance with the 6 Hard Validity Rules, exact Slide 5 replication ($J = 59.0$), and Slide 8 JSON export without UI distractions.

### Consequences
V1 deliverables focus on Python 3.11 CLI, algorithmic optimization, and static Matplotlib schematic overlays. V2 can be initiated as a separate milestone after V1 completion.

---

## [DEC-002] Pre-Finalization Verification Gate for Flagged Dataset & Rule Claims

**Date**: 2026-10-15  
**Status**: Accepted  

### Context
`v2.md` contains 9 specific claims regarding dataset structure, node input formats, scale calculation formulas, room categorizations, and capacity limits (e.g. 5 nodes vs 20 nodes per hub) that must be verified against official organizer materials.

### Decision
Keep `SPEC.md` in `DRAFT (Pending Source & Dataset Verification)` and explicitly enumerate the 9 flagged claims in Section 7 before finalizing the spec and releasing the planning lock.

### Rationale
Prevents building algorithmic components on assumptions that might conflict with the organizers' automated evaluation script.

---

## [DEC-003] Zero-Overlap ($O = 0$) Single-Door Transit via Parallel Lanes

**Date**: 2026-10-15  
**Status**: Accepted  

### Context
Single-door rooms require cables to enter and exit through the same door. Slide 6 & 7 penalize collinear overlap $O$ at $1.0/\text{m}$ if segments run within $1\text{ mm}$ on the same line, but explicitly note that parallel runs separated by $> 1\text{ mm}$ incur zero overlap ($O = 0$).

### Decision
Synthesize two parallel doorway tracks (Lane A and Lane B) across each door threshold separated by $\ge 20\text{ mm}$ ($20 \times$ the $1\text{ mm}$ tolerance). Route incoming legs along Lane A and outgoing legs along Lane B.

### Rationale
Completely eliminates overlap penalties on single-door leaf rooms ($O = 0.0\text{ m}$), saving up to several metres of cost per plan.

---

## [DEC-004] Direction-Aware State-Space Pathfinding ($v, d_{\text{in}}$)

**Date**: 2026-10-15  
**Status**: Accepted  

### Context
Slide 7 charges $+0.5$ for every direction change (bends $B$), including $90^\circ$ turns and $180^\circ$ reversals occurring at vertices, fixed nodes, and hubs. Passing straight through a node charges 0.

### Decision
Define graph search states as $(v, d_{\text{in}})$ with $d_{\text{in}} \in \{\text{N}, \text{S}, \text{E}, \text{W}\}$ and embed arrival/departure direction states into the Held-Karp dynamic programming formulation.

### Rationale
Ensures the micro-router directly optimizes the true competition metric $J = L + 5X + O + 0.5B$ rather than plain Manhattan distance.

---

## [DEC-005] Configurable Hub Capacity Limits (`max_nodes_per_hub`)

**Date**: 2026-10-15  
**Status**: Accepted  

### Context
`v2.md` notes an update where max nodes per hub was reduced from 20 to 5 for ResPlan floor plans.

### Decision
Configure `max_nodes_per_hub = 5` and `max_area_m2 = 100.0` in `SystemConfig`, but expose them as CLI overrides (`--max-nodes`, `--max-area`) so the engine can immediately adapt if the official checker runs with 20 nodes.

---

*Last updated: 2026-10-15*
