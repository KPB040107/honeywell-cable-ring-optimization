import json

with open("results/recovery/empirical_recovery_results.json", "r") as f:
    results = json.load(f)

for r in results:
    if r["plan_id"] == 345536:
        print("Plan 345536 in empirical_recovery_results:")
        print(json.dumps(r, indent=2))
