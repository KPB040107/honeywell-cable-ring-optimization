import sys

# 1. Update parser.py
with open("src/resplan_router/phase1_ingestion/parser.py", "r", encoding="utf-8") as f:
    parser_code = f.read()

old_parser_target = """    # 1. Obstacle union O = wall ∪ window ∪ front_door
    obs_components: List[BaseGeometry] = []
    obs_components.extend([w for w in walls if w and not w.is_empty])
    obs_components.extend([w for w in windows if w and not w.is_empty])
    if front_doors:
        obs_components.extend([fd for fd in front_doors if fd and not fd.is_empty])

    if obs_components:
        obstacles = shapely.unary_union(obs_components)
    else:
        obstacles = Polygon()
    obstacles = shapely.make_valid(obstacles)

    # 2. Free space F = inner \\ (wall ∪ window)
    wall_win_components: List[BaseGeometry] = []
    wall_win_components.extend([w for w in walls if w and not w.is_empty])
    wall_win_components.extend([w for w in windows if w and not w.is_empty])

    if wall_win_components:
        wall_win_union = shapely.unary_union(wall_win_components)
        free_space = scaled_inner.difference(wall_win_union)
    else:
        free_space = scaled_inner
    free_space = shapely.make_valid(free_space)"""

new_parser_replacement = """    # Carve interior doors out of walls so doorways remain in free space
    valid_doors = [d for d in (doors or []) if d and not d.is_empty]
    door_union = shapely.unary_union(valid_doors) if valid_doors else None

    carved_walls = []
    for w in walls:
        if w and not w.is_empty:
            if door_union is not None and not door_union.is_empty and w.intersects(door_union):
                diff = w.difference(door_union)
                if not diff.is_empty:
                    carved_walls.append(diff)
            else:
                carved_walls.append(w)

    # 1. Obstacle union O = (wall \\ door) ∪ window ∪ front_door
    obs_components: List[BaseGeometry] = []
    obs_components.extend(carved_walls)
    obs_components.extend([w for w in windows if w and not w.is_empty])
    if front_doors:
        obs_components.extend([fd for fd in front_doors if fd and not fd.is_empty])

    if obs_components:
        obstacles = shapely.unary_union(obs_components)
    else:
        obstacles = Polygon()
    obstacles = shapely.make_valid(obstacles)

    # 2. Free space F = inner \\ ((wall \\ door) ∪ window ∪ front_door)
    if obs_components:
        free_space = scaled_inner.difference(obstacles)
    else:
        free_space = scaled_inner
    free_space = shapely.make_valid(free_space)"""

if old_parser_target in parser_code:
    parser_code = parser_code.replace(old_parser_target, new_parser_replacement)
    with open("src/resplan_router/phase1_ingestion/parser.py", "w", encoding="utf-8") as f:
        f.write(parser_code)
    print("Updated parser.py successfully")
else:
    print("Target in parser.py not matched directly, checking normalized...")
    # Attempt with normalized newlines
    old_norm = old_parser_target.replace("\r\n", "\n")
    p_norm = parser_code.replace("\r\n", "\n")
    if old_norm in p_norm:
        p_norm = p_norm.replace(old_norm, new_parser_replacement.replace("\r\n", "\n"))
        with open("src/resplan_router/phase1_ingestion/parser.py", "w", encoding="utf-8") as f:
            f.write(p_norm)
        print("Updated parser.py successfully (normalized newlines)")
    else:
        print("ERROR: Target in parser.py not found!")
        sys.exit(1)
