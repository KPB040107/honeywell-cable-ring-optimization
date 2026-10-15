# DECISIONS.md — Architecture & Design Decision Records

## ADR-001: Separation of Exact Optimization vs. Heuristics
- **Status**: Accepted
- **Context**: Problem Statement 4 requires joint hub placement, room assignment, and cable routing.
- **Decision**:
  1. Lexicographic optimization: Minimize hub count $K$ first, then minimize routing cost $J$.
  2. Hub count lower bound $K_{\min}$ is exact.
  3. Contiguous zone partitioning is formulated as an exact CP-SAT Mixed-Integer Program with Single-Commodity Flow (SCF) constraints; escalating $K \leftarrow K + 1$ when cut vertices cause infeasibility.
  4. Device TSP sequencing is exact via Held-Karp bitmask dynamic programming for $m \le 12$ ($N_{\max} = 5$ default is globally optimal). OR-Tools Routing Model metaheuristic is used only for $m > 12$.
  5. Micro-routing is exact state-space $A^*$/Dijkstra on the obstacle-free rectilinear grid.
  6. Overall multi-hub cable deconfliction utilizes sequential routing with soft-obstacle penalization.

## ADR-002: Synthetic 6-Room Benchmark as Ground Truth Fixture
- **Status**: Accepted
- **Context**: No pre-installed raw ResPlan JSON sample exists in the local workspace. Slide 5/8 provides the authoritative evaluation layout ($118\text{ m}^2$, 6 rooms + bathroom).
- **Decision**: Construct `tests/fixtures/schematic_6room.json` matching the exact room geometry, doors, and areas from Slide 5/8. Implement a documented real ResPlan JSON parser with validation and provide an adapter script to ingest actual Kaggle samples when supplied.

## ADR-003: Transit-Only Corridors and Excluded Rooms
- **Status**: Accepted
- **Context**: Floor plans may have corridors or passages that are excluded from device placement but necessary for transit between eligible rooms.
- **Decision**: Transit corridors are modeled as transit-only vertices in $G_{\text{macro}}$ with $0$ devices and $0$ area load. They never host hubs. Outer boundaries, exterior walls, and solid obstacles strictly forbid transit.

## ADR-004: Dual-Lane Portal Offsets for Single-Door Spur Rooms
- **Status**: Accepted
- **Context**: Single-door rooms (e.g., Bedroom 2, Storage) require cable to enter and exit through the same doorway without self-intersecting or collinear overlapping.
- **Decision**: Extract dual-lane waypoints ($p_A, p_B$ separated by $2\delta$) at doorways. When routing closed loops into and out of leaf rooms, assign Lane A to arrival and Lane B to departure based on tour orientation.
