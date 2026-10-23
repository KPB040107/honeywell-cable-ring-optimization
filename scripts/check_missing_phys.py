import json

with open("results/recovery/empirical_recovery_results.json", "r") as f:
    results = json.load(f)

rank1_14 = [5460, 15000, 15678, 145814, 227794, 284019, 301603, 345536, 399279, 413814, 586490, 589710, 622584, 952547]
rank2_8 = [452, 778, 4048, 7447, 8272, 8716, 16162, 16612]
phys_22 = set(rank1_14 + rank2_8)

all_pids_850 = set(r["plan_id"] for r in results)
print("Physical 22 in 850 audited candidates:", len(phys_22 & all_pids_850))
print("Missing from 850:", phys_22 - all_pids_850)

rec_pids = set(r["plan_id"] for r in results if r["recovered_is_valid"])
unrec_pids = set(r["plan_id"] for r in results if not r["recovered_is_valid"])

print("In rec_pids?", phys_22 & rec_pids)
print("In unrec_pids?", phys_22 & unrec_pids)
