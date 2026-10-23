import json

with open("results/recovery/phase16_failure_ledger.json", "r", encoding="utf-8") as f:
    ledger = json.load(f)

plans = ledger["discrepancy_ledger_plans"]
rank1 = [p for p in plans if p["classification_rank"] == 1]
print(f"Rank 1 count: {len(rank1)}")
for p in rank1:
    print(f"PID: {p['plan_id']}, primary: {p['primary_cause']}, sig: {p['failure_signature']}")
    print(f"  error: {p['error_details']}")

rank2 = [p for p in plans if p["classification_rank"] == 2]
print(f"\nRank 2 count: {len(rank2)}")
for p in rank2:
    print(f"PID: {p['plan_id']}, primary: {p['primary_cause']}, sig: {p['failure_signature']}")
    print(f"  error: {p['error_details']}")
