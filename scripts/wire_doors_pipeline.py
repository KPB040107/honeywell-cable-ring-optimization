import shapely

# 1. Update resplan_adapter.py
with open("src/resplan_router/phase1_ingestion/resplan_adapter.py", "r", encoding="utf-8") as f:
    adapter_code = f.read()

adapter_target = """    return MetricFloorPlan(
        plan_id=plan_id,
        scale_units_per_m=round(scale, 6),
        total_area_m2=round(raw_area, 4),
        inner_wkt=healed_inner.wkt,
        free_space_wkt=free_space.wkt,
        obstacles_wkt=obstacles.wkt,
        rooms=rooms_dict,
        nodes=nodes_dict,
    )"""

adapter_replacement = """    doors_union = shapely.unary_union(metric_doors) if metric_doors else None
    doors_wkt = doors_union.wkt if (doors_union is not None and not doors_union.is_empty) else None

    front_doors_union = shapely.unary_union(metric_front_doors) if metric_front_doors else None
    front_doors_wkt = front_doors_union.wkt if (front_doors_union is not None and not front_doors_union.is_empty) else None

    return MetricFloorPlan(
        plan_id=plan_id,
        scale_units_per_m=round(scale, 6),
        total_area_m2=round(raw_area, 4),
        inner_wkt=healed_inner.wkt,
        free_space_wkt=free_space.wkt,
        obstacles_wkt=obstacles.wkt,
        doors_wkt=doors_wkt,
        front_doors_wkt=front_doors_wkt,
        rooms=rooms_dict,
        nodes=nodes_dict,
    )"""

norm_adapter = adapter_code.replace("\r\n", "\n")
norm_adapter_target = adapter_target.replace("\r\n", "\n")
norm_adapter_replacement = adapter_replacement.replace("\r\n", "\n")
assert norm_adapter_target in norm_adapter, "Target not in resplan_adapter.py"
norm_adapter = norm_adapter.replace(norm_adapter_target, norm_adapter_replacement)

with open("src/resplan_router/phase1_ingestion/resplan_adapter.py", "w", encoding="utf-8") as f:
    f.write(norm_adapter)
print("Updated resplan_adapter.py successfully!")

# 2. Update parser.py
with open("src/resplan_router/phase1_ingestion/parser.py", "r", encoding="utf-8") as f:
    parser_code = f.read()

parser_target = """    return MetricFloorPlan(
        plan_id=plan_id,
        scale_units_per_m=scale,
        total_area_m2=round(total_area_m2, 4),
        inner_wkt=healed_inner.wkt,
        free_space_wkt=free_space.wkt,
        obstacles_wkt=obstacles.wkt,
        rooms=rooms_dict,
        nodes=nodes_dict,
    )"""

parser_replacement = """    doors_union = shapely.unary_union(metric_doors) if metric_doors else None
    doors_wkt = doors_union.wkt if (doors_union is not None and not doors_union.is_empty) else None

    front_doors_union = shapely.unary_union(metric_front_doors) if metric_front_doors else None
    front_doors_wkt = front_doors_union.wkt if (front_doors_union is not None and not front_doors_union.is_empty) else None

    return MetricFloorPlan(
        plan_id=plan_id,
        scale_units_per_m=scale,
        total_area_m2=round(total_area_m2, 4),
        inner_wkt=healed_inner.wkt,
        free_space_wkt=free_space.wkt,
        obstacles_wkt=obstacles.wkt,
        doors_wkt=doors_wkt,
        front_doors_wkt=front_doors_wkt,
        rooms=rooms_dict,
        nodes=nodes_dict,
    )"""

norm_parser = parser_code.replace("\r\n", "\n")
norm_parser_target = parser_target.replace("\r\n", "\n")
norm_parser_replacement = parser_replacement.replace("\r\n", "\n")
assert norm_parser_target in norm_parser, "Target not in parser.py"
norm_parser = norm_parser.replace(norm_parser_target, norm_parser_replacement)

with open("src/resplan_router/phase1_ingestion/parser.py", "w", encoding="utf-8") as f:
    f.write(norm_parser)
print("Updated parser.py successfully!")

# 3. Update portal_extractor.py
with open("src/resplan_router/phase2_topology/portal_extractor.py", "r", encoding="utf-8") as f:
    portal_code = f.read()

door_extract_target = """def extract_door_portals(
    plan: MetricFloorPlan,
    raw_doors: Optional[Sequence[Any]] = None,
    raw_entrance: Optional[Any] = None,
) -> List[DoorOrPassagePortal]:"""

door_extract_replacement = """def extract_door_portals(
    plan: MetricFloorPlan,
    raw_doors: Optional[Sequence[Any]] = None,
    raw_entrance: Optional[Any] = None,
) -> List[DoorOrPassagePortal]:
    if raw_doors is None and getattr(plan, "doors_geom", None) is not None:
        raw_doors = plan.doors_geom
    if raw_entrance is None and getattr(plan, "front_doors_geom", None) is not None:
        raw_entrance = plan.front_doors_geom"""

all_extract_target = """def extract_all_portals(
    plan: MetricFloorPlan,
    raw_doors: Optional[Sequence[Any]] = None,
    raw_entrance: Optional[Any] = None,
) -> List[DoorOrPassagePortal]:"""

all_extract_replacement = """def extract_all_portals(
    plan: MetricFloorPlan,
    raw_doors: Optional[Sequence[Any]] = None,
    raw_entrance: Optional[Any] = None,
) -> List[DoorOrPassagePortal]:
    if raw_doors is None and getattr(plan, "doors_geom", None) is not None:
        raw_doors = plan.doors_geom
    if raw_entrance is None and getattr(plan, "front_doors_geom", None) is not None:
        raw_entrance = plan.front_doors_geom"""

norm_portal = portal_code.replace("\r\n", "\n")
norm_door_target = door_extract_target.replace("\r\n", "\n")
norm_door_repl = door_extract_replacement.replace("\r\n", "\n")
norm_all_target = all_extract_target.replace("\r\n", "\n")
norm_all_repl = all_extract_replacement.replace("\r\n", "\n")

assert norm_door_target in norm_portal, "door target not in portal_extractor.py"
norm_portal = norm_portal.replace(norm_door_target, norm_door_repl)
assert norm_all_target in norm_portal, "all target not in portal_extractor.py"
norm_portal = norm_portal.replace(norm_all_target, norm_all_repl)

with open("src/resplan_router/phase2_topology/portal_extractor.py", "w", encoding="utf-8") as f:
    f.write(norm_portal)
print("Updated portal_extractor.py successfully!")
