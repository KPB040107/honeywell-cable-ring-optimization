--- SLIDE 1 ---
Project scope and expected results
Joint optimization of hub placement and closed-ring cable routing on ResPlan floor plans
Research exercise: results are not a building-services design.
The system decides
1
How many hubs, and where each one goes
2
Which rooms and nodes each hub serves
3
The order in which each hub’s nodes are connected
4
The physical cable route of every closed ring
Rules every design must meet
•  Nodes: one fixed node in every eligible living room, bedroom, kitchen and storage room.
•  Capacity: each hub serves at most 100 m² of assigned eligible-room area and 5 nodes.
•  Zones: a hub’s rooms form one connected zone; every node belongs to exactly one hub.
•  Ring: H → N1 → … → Nn → same H. Closed and non-branching, each node once.
•  Routes: horizontal and vertical runs, between rooms only through doors and open passages, inside the inner boundary, never through walls, windows or external areas.
•  Invalid: open-ended routes, branches, skipped or repeated nodes, a ring ending at another hub.
Expected outcome
•  A working prototype that processes an unseen ResPlan plan with no manual adjustment.
•  Visual overlay: room-to-hub assignment, nodes, hubs, connection order and full ring routes. It must show routes crossing only at doors or open passages, avoiding walls and windows, and returning to the starting hub.
•  JSON output: supports validation and scoring.
•  Ranking: fewest hubs first, then lowest cost J.
Major work elements
•  ResPlan geometry, scale conversion and eligible-room identification
•  Capacity-constrained hub placement and room assignment
•  Contiguous zone generation from door and open-passage links
•  Closed-ring node sequencing
•  Obstacle-aware axis-aligned routing through doors and open passages
•  Wall, window and external-area avoidance
•  Optimization of hub count, cable length, crossings, overlap and bends
•  Design validation and floor-plan visualization
Required skills
•  Python
•  Computational geometry
•  Graph algorithms
•  Optimization and pathfinding
•  Travelling-salesperson methods
•  Data visualization
Resources
•  Dataset: ResPlan, 17,000 residential plans (Kaggle and GitHub).
     kaggle.com/datasets/resplan/resplan
•  Input: one JSON file per plan from the organisers.
UR Hackathon 2026H2  |  Problem statement  |  1 / 8
--- SLIDE 2 ---
Problem statement (1/2): Task and inputs
Joint optimization of hub placement and closed-ring cabling on ResPlan floor plans
UR Hackathon 2026H2  |  Problem statement  |  2 / 8
Research exercise: results are not a building-services design.
1  Place hubs
choose how many, and where
2  Assign rooms
each room + its node to one hub
3  Order nodes
one closed sequence per hub
4  Route cable
axis-aligned, through doors
Given for every plan
•  Geometry in drawing units. Convert to metres with the scale on slide 4; invalid polygons are repaired.
•  Eligible rooms: living, bedroom, kitchen, storage. Each polygon is one room with one node.
•  Node points are fixed inputs: one arbitrary interior point per room (0.5 m from walls). Teams cannot move them.
•  Excluded: bathroom, balcony, stair, garden, pool, parking, land. No node and no hub.
•  Benchmark plans: the organisers keep only plans a reference solver can solve (no room over 100 m²).
Capacity of each hub
≤ 100 m²
of assigned eligible-room area
≤ 5 nodes
assigned to the hub
Both limits apply at once.
Hubs needed ≥ max( ceil(area/100), ceil(nodes/5) )
Required ring per hub
H → N1 → N2 → … → Nn → H
•  Starts and ends at the same hub
•  Visits every assigned node exactly once
•  No branches; never ends at another hub
•  Every node belongs to exactly one ring
•  Retracing a door to reach a single-door room is allowed; shared line length counts as overlap
Dataset: ResPlan (17,000 residential plans, Kaggle / GitHub). 
--- SLIDE 3 ---
Problem statement (2/2): Rules, ranking and deliverables
A design must pass every rule before it is ranked
UR Hackathon 2026H2  |  Problem statement  |  3 / 8
Research exercise: results are not a building-services design.
Hard rules: a plan is invalid if any is broken
1
Coverage: every eligible room and its node is assigned to exactly one hub; none omitted, split or duplicated.
2
Capacity: each hub serves at most 100 m² of eligible-room area and at most 5 nodes.
3
Zone: a hub's rooms are connected through doors or open passages only. Windows, wall-only adjacency and the entrance do not count.
4
Ring: one closed ring per hub: H → every assigned node once → same H, no branches. Retracing a door to reach a single-door room is allowed; shared line length counts as overlap.
5
Free space: cable stays inside the inner boundary, outside walls and windows, runs only horizontally or vertically, and passes between rooms only through door or open-passage gaps.
6
Hub point: inside an eligible room assigned to that hub, at least 0.5 m from walls. A hub and a node may share a room.
Ranking of valid designs
Fewest hubs first, then lowest cost (L + X + O + B)
H
Fewest hubs (always decides first)
L
Cable length, returns included (1/m)
X
Crossings, any rings (5 each)
O
Overlap on the same line (1/m)
B
Bends, all 90° turns (0.5 each)
Tie-break: smaller assigned-area gap between hubs
Deliverables per plan
•  Floor-plan overlay: zones, hub and node IDs, visiting order, full rings with return leg, violations in red
•  JSON with the same hubs, sequences, polylines and lengths
--- SLIDE 4 ---
Scale: from drawing units to metres
ResPlan coordinates are drawing units, not metres. Two values stored in every plan give the conversion.
Research exercise: results are not a building-services design.
Example plan: 200 × 180 drawing units
Bedroom 1
Kitchen
Bath
Bedroom 2
Living
Balcony: outside inner
inner (shaded)
inner.area = 36,000 units²
area = 110 m²
Dark bars: walls.  Shaded: inner.  Dashed: outside inner.
200 units ≈ 11.1 m
180 units
≈ 9.9 m
inner.area   (drawing units²)
Area of the plan’s inner polygon: everything inside the outer walls, meaning all rooms and the partition walls between them. Balconies and outdoor areas are outside it. Example: 36,000 units².
area   (m²)
Floor area of that same footprint in square metres. It is a single number stored in the plan. Example: 110 m². Do not use net_area: it is 0 in 32% of plans.
Scale = drawing units per metre
scale = sqrt( inner.area / area )
= sqrt( 36,000 / 110 )  ≈  18.1 units per metre
Convert once per plan, before any limit or clearance check.
•  Length:  metres = units ÷ scale   (90 units ≈ 5.0 m)
•  Area:  m² = units² ÷ scale²   (100 m² = 32,700 units² here)
•  Clearance:  0.5 m = 9.0 units here
scale = (plan["inner"].area / plan["area"]) ** 0.5
UR Hackathon 2026H2  |  Problem statement  |  4 / 8
--- SLIDE 5 ---
Expected solution: illustrative schematic
Invented 6-room layout, not a ResPlan plan. Two zones, each with one hub and one ring that returns to it.
UR Hackathon 2026H2  |  Problem statement  |  5 / 8
Research exercise: results are not a building-services design.
Bedroom 1
16 m²
Bedroom 2
16 m²
Kitchen
16 m²
Bathroom
no node
Bedroom 3
15 m²
Living
40 m²
Storage
15 m²
Entrance
Single-door room: cable goes in and back out through the same door (two parallel runs)
1
2
3
4
1
2
3
4
N1
N2
N3
N4
N5
N6
H1
H2
How to read
Hub (one colour per zone)
Node (fixed point in each eligible room)
Cable; numbers give visiting order
Last number is the return to the hub
Excluded room; gaps in walls are doors
Per-hub summary
H1
Living, Bedrooms 1 and 3
71 m²  |  3 nodes  |  23.8 m, 8 bends
H2
Bedroom 2, Kitchen, Storage
47 m²  |  3 nodes  |  25.2 m, 12 bends
What a reviewer confirms
✓  Each node once; ring ends at its own hub
✓  ≤ 100 m² and ≤ 5 nodes per hub
✓  Each zone connected through doors only
✓  Axis-aligned; no wall or window crossed
✓  2 hubs = lower bound (118 m², 6 nodes)
--- SLIDE 6 ---
Evaluation: objective and metric definitions
The checker recomputes every metric from the submitted polylines; teams choose how to optimise
UR Hackathon 2026H2  |  Problem statement  |  6 / 8
Research exercise: results are not a building-services design.
Fewest hubs wins. Ties go to the lowest cost J.
J = L + 5X + O + 0.5B, in metres: cable length, plus 5 per crossing, 1 per metre of overlap, 0.5 per bend. Hard rules on slide 3 apply first.
Metric definitions (per plan, computed by the checker)
H
Hubs H: number of selected hubs; always compared first.
L
Cable length L (1 per m): sum of polyline lengths in metres over every leg of every ring, return legs included.
X
Crossings X (5 each): points where two cables meet away from a node or hub, inside one ring or between rings.
O
Overlap O (1 per m): total length in metres where two legs run along the same line. A retrace on the same line counts; parallel runs do not.
B
Bends B (0.5 each): direction changes along each closed ring, nodes included. Cable is axis-aligned, so every turn is 90°; a reversal counts once.
Tie-break: equal J is broken by a smaller assigned-area gap between hubs.  Tolerance: 1 mm. Weights are starting values, fixed after reference runs.
Scoring across plans
•  Values reported in the JSON are ignored
•  Per plan, valid designs are ranked by hub count, then by cost J
•  Rank points add up over hidden, unseen plans
•  An invalid or missing plan scores zero
Left for the teams
•  The optimisation model and algorithm
•  Any internal cost or weighting they like
•  Only the final hub count and cost J are scored
--- SLIDE 7 ---
Metric definitions with examples
The checker computes each metric from the submitted polylines. The drawings show what counts and what does not.
Research exercise: results are not a building-services design.
Cable length (L)
Total length of every leg of every ring, including the leg back to the hub, measured along the polyline in metres.
4 m
2 m
3 m
Counts: 4 + 2 + 3 + 3 = 12 m, return leg included
3 m out + 3 m back
A retraced stretch counts each time it is run: 6 m
Crossings (X)
A point where two cables meet away from a node or hub. It counts inside one ring and between rings, once per point.
Counts: cables meet in the open, X = 1
Does not count: legs meet at a node, X = 0
Overlap (O)
Length where cable lies on top of other cable on the same line, such as a retrace. A stretch covered by k legs counts (k − 1) times its length.
2 m shared
Counts: 2 m on the same line, O = 2 m
Does not count: parallel runs on different lines, O = 0
Bends (B)
A point where the cable changes direction, at a vertex, a node or the hub. All turns are 90°, and a reversal counts once.
Counts: each turn is one bend, B = 2
Does not count: straight through, B = 0
Tolerance 1 mm: points closer than that are the same point, and lines within 1 mm of each other are the same line. Each metric is counted per plan, summed over all rings.
UR Hackathon 2026H2  |  Problem statement  |  7 / 8
3 m
3 m
3 m
--- SLIDE 8 ---
Data format: input and output JSON
One input file per plan ; one output file per plan from each team ; and a visualizations for final presentation 
UR Hackathon 2026H2  |  Problem statement  |  8 / 8
Research exercise: results are not a building-services design.
Output file (team submits)
{
  "plan_id": "PLAN_0123", "units": "m",
  "hubs": [
    {
      "hub_id": "H1", "room_id": "R05",
      "location": {"x": 5.2, "y": 3.6},
      "assigned_rooms": ["R05", "R01", "R04"],
      "assigned_nodes": ["N1", "N2", "N3"],
      "closed_sequence": ["H1","N1","N3","N2","H1"],
      "route_segments": [
        {"from":"H1","to":"N1","length_m":4.8,
         "polyline":[[5.2,3.6],[7.6,3.6],[7.6,1.2]]},
        {"from":"N1","to":"N3","length_m":7.1,
         "polyline":[[7.6,1.2],[6.6,1.2],[6.6,2.2],[1.5,2.2]]},
        ... N3 to N2 and N2 to H1 follow
      ],
      "ring_length_m": 23.8
    },
    ... H2 has the same structure
  ],
  "reported_metrics": {"hub_count": 2, "total_length_m": 49.0}
}
Abridged: H1 is the ring on slide 5. Output in metres (units ÷ scale), origin lower-left, y up.