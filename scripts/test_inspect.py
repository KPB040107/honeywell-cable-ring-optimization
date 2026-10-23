import pickle
import json
from pathlib import Path

print("Reading ResPlan.pkl...")
with open("ResPlan.pkl", "rb") as f:
    data = pickle.load(f)

print(f"Data type: {type(data)}, length: {len(data)}")
if isinstance(data, list):
    print("Item 0 keys:", list(data[0].keys()))
    print("Item 0 id:", data[0].get("id"))
elif isinstance(data, dict):
    print("Dict keys sample:", list(data.keys())[:5])

print("Reading split.json...")
with open("split.json", "r", encoding="utf-8") as f:
    splits = json.load(f)

print(f"Split keys: {list(splits.keys())}")
for k, v in splits.items():
    print(f"  {k}: {len(v)} plans")
