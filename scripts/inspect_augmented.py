"""Inspect augmented split zero rooms."""
import json
import pickle

with open("split.json", "r") as f:
    splits = json.load(f)

with open("ResPlan.pkl", "rb") as f:
    resplan_list = pickle.load(f)
resplan_map = {r["id"]: r for r in resplan_list}

aug_pids = splits["augmented"]
print(f"Total augmented plans: {len(aug_pids)}")

zero_rooms = []
for pid in aug_pids:
    r = resplan_map[pid]
    # Check eligible rooms
    has_room = False
    for cat in ["bedroom", "living", "kitchen", "storage"]:
        if r.get(cat) is not None:
            has_room = True
            break
    if not has_room:
        zero_rooms.append(pid)

print(f"Augmented plans with zero eligible room keys: {len(zero_rooms)}")
if zero_rooms:
    print(f"Sample zero room PIDs: {zero_rooms[:10]}")
    sample_r = resplan_map[zero_rooms[0]]
    print(f"Sample plan {zero_rooms[0]} keys: {list(sample_r.keys())}")
    for k in sample_r.keys():
        val = sample_r[k]
        print(f"  {k}: {type(val)} {getattr(val, 'is_empty', '') if hasattr(val, 'is_empty') else val if isinstance(val, (int, float, str)) else ''}")
