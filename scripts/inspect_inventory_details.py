"""Inspect details of dataset inventory and categories."""
import json
import pickle

with open("results/dataset_inventory.json", "r") as f:
    inv = json.load(f)

print("Inventory metadata:", inv.get("metadata"))
print("Inventory split distribution:", inv.get("split_distribution"))
print("Eligible rooms distribution overall:", inv.get("eligible_rooms_distribution", {}).get("overall"))

with open("results/dataset_reconciliation_audit.json", "r") as f:
    recon = json.load(f)

print("Recon split breakdown:")
print(json.dumps(recon.get("per_split_breakdown"), indent=2))
print("strictly_infeasible_gt_100_count:", recon.get("strictly_infeasible_gt_100_count"))
print("tight_95_to_100_count:", len(recon.get("tight_95_to_100_pids", [])))
print("anomalous_geometry_count:", len(recon.get("anomalous_geometry_pids", [])))
print("zero_rooms_count:", len(recon.get("zero_rooms_pids", [])))
