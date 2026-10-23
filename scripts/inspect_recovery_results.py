import json
from collections import Counter

with open("results/recovery/empirical_recovery_results.json", "r") as f:
    results = json.load(f)

valid_counter = Counter(r.get("recovered_is_valid") for r in results)
cat_counter = Counter(r.get("recovery_category") for r in results)

print("recovered_is_valid:", valid_counter)
print("recovery_category:", cat_counter)

unrecovered = [r for r in results if not r.get("recovered_is_valid")]
print(f"Total unrecovered: {len(unrecovered)}")
rank1_14 = [5460, 15000, 15678, 145814, 227794, 284019, 301603, 345536, 399279, 413814, 586490, 589710, 622584, 952547]
rank2_8 = [452, 778, 4048, 7447, 8272, 8716, 16162, 16612]
phys_22 = set(rank1_14 + rank2_8)

unrec_pids = set(r["plan_id"] for r in unrecovered)
print("Physical 22 in unrecovered:", len(phys_22 & unrec_pids))
cadastral_181 = unrec_pids - phys_22
print(f"Cadastral defect unrecovered (181): {len(cadastral_181)}")
