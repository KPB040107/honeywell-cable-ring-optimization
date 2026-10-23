"""Benchmark audit loop over all 17,000 plans in ResPlan.pkl."""
import time
import pickle
import json
import hashlib
from pathlib import Path
import shapely

start_time = time.time()
print("Starting audit benchmark...")

# 1. SHA-256
t0 = time.time()
with open("ResPlan.pkl", "rb") as f:
    resplan_bytes = f.read()
resplan_sha256 = hashlib.sha256(resplan_bytes).hexdigest()

with open("split.json", "rb") as f:
    split_bytes = f.read()
split_sha256 = hashlib.sha256(split_bytes).hexdigest()
print(f"Computed SHA256 in {time.time() - t0:.2f}s")
print(f"ResPlan.pkl: {resplan_sha256} ({len(resplan_bytes)} bytes)")
print(f"split.json:  {split_sha256} ({len(split_bytes)} bytes)")

# 2. Unpickle
t0 = time.time()
resplan_list = pickle.loads(resplan_bytes)
splits = json.loads(split_bytes.decode("utf-8"))
print(f"Loaded ResPlan.pkl ({len(resplan_list)} records) and split.json in {time.time() - t0:.2f}s")

# 3. Basic loop over records
t0 = time.time()
ids = set()
dup_ids = []
missing_id = 0
invalid_inner = 0
negative_coords = 0
empty_inner = 0
scales = []
area_discrepancies = []
key_sets = set()

for idx, r in enumerate(resplan_list):
    pid = r.get("id")
    if pid is None:
        missing_id += 1
    elif pid in ids:
        dup_ids.append(pid)
    else:
        ids.add(pid)
    
    k_sig = tuple(sorted(r.keys()))
    key_sets.add(k_sig)
    
    inner = r.get("inner")
    if inner is None or (hasattr(inner, "is_empty") and inner.is_empty):
        empty_inner += 1
        continue
    
    if not inner.is_valid:
        invalid_inner += 1
    
    bounds = inner.bounds
    if bounds[0] < 0 or bounds[1] < 0:
        negative_coords += 1
        
    area_meta = float(r.get("area", 0.0))
    if area_meta > 0 and inner.area > 0:
        scale = (inner.area / area_meta) ** 0.5
        scales.append(scale)

print(f"Iterated all 17,000 records in {time.time() - t0:.2f}s")
print(f"Unique IDs: {len(ids)}, Duplicate IDs: {len(dup_ids)}, Missing IDs: {missing_id}")
print(f"Invalid inner geometries: {invalid_inner}, Empty inner: {empty_inner}, Negative coords: {negative_coords}")
print(f"Distinct schema key signatures: {len(key_sets)}")
print(f"Total time elapsed: {time.time() - start_time:.2f}s")
