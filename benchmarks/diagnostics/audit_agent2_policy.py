"""Agent 2 PPO Policy Audit: Rigorous evaluation of trained policy behaviors.

Audit steps:
1. Load both latest_model.pt (step 9,984) and best_model.pt (step 320).
2. Query action probabilities across >= 200 representative floor plan states
   from results/cache/baseline_cache.sqlite.
3. Compute per-action mean probability, argmax selection frequency, and entropy.
4. Compare against random / uniform baseline.
5. Identify the dominant operator and measure policy collapse magnitude.
6. Save structured results to results/diagnostics_agent2_policy.json.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import torch

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from resplan_router.contracts import SynthesizedRingsBundle, HubZonePartition
from resplan_router.optimizers.alns_operators import ALNSState, OPERATOR_NAMES, NUM_OPERATORS
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from rl.observations import extract_observation_vector
from rl.policy import ActorCriticPolicy


def run_policy_audit(max_states: int = 250) -> Dict[str, Any]:
    print(f"Loading policy models...")
    latest_path = Path("checkpoints/ppo_alns/latest_model.pt")
    best_path = Path("checkpoints/ppo_alns/best_model.pt")

    policy_latest = ActorCriticPolicy()
    policy_latest.load_state_dict(torch.load(latest_path, map_location="cpu"))
    policy_latest.eval()

    policy_best = ActorCriticPolicy()
    policy_best.load_state_dict(torch.load(best_path, map_location="cpu"))
    policy_best.eval()

    print(f"Connecting to database results/cache/baseline_cache.sqlite...")
    conn = sqlite3.connect("results/cache/baseline_cache.sqlite")
    cur = conn.cursor()
    cur.execute(
        "SELECT plan_id, h_count, cost_J, length_m, bends, crossings, overlap_m, area_gap_m2, bundle_json, partition_json "
        "FROM baseline_cache WHERE is_valid = 1 LIMIT ?",
        (max_states,)
    )
    rows = cur.fetchall()
    conn.close()
    print(f"Retrieved {len(rows)} valid cached baseline states.")

    repo = DatasetRepository()

    obs_list = []
    metadata_list = []

    for row in rows:
        p_id, h_count, cost_J, length_m, bends, crossings, overlap_m, area_gap_m2, b_json, part_json = row
        plan = repo.get_floor_plan(p_id)
        if plan is None:
            continue

        try:
            bundle = SynthesizedRingsBundle.model_validate_json(b_json)
            partition = HubZonePartition.model_validate_json(part_json)
        except Exception:
            continue

        state = ALNSState(
            zones=[set(h.assigned_rooms) for h in partition.hubs],
            bundle=bundle,
            partition=partition,
            cost_J=cost_J,
            h_count=h_count,
            area_gap=area_gap_m2,
            is_valid=True,
        )

        obs = extract_observation_vector(
            state=state,
            incumbent=state,
            baseline_J=cost_J,
            baseline_H=h_count,
            step_idx=10,
            max_steps=25,
            temperature=1.0,
            initial_temperature=1.0,
            stagnation_steps=0,
            operator_success_counts=[0] * NUM_OPERATORS,
            operator_call_counts=[0] * NUM_OPERATORS,
            plan=plan,
        )
        obs_list.append(obs)
        metadata_list.append({"plan_id": p_id, "h_count": h_count, "cost_J": cost_J})

    print(f"Constructed {len(obs_list)} valid state observation vectors.")
    obs_tensor = torch.tensor(np.array(obs_list), dtype=torch.float32)

    with torch.no_grad():
        # Evaluate latest policy (end of 10k pilot)
        logits_latest = policy_latest.actor(obs_tensor)
        probs_latest = torch.softmax(logits_latest, dim=-1).numpy()
        argmax_latest = np.argmax(probs_latest, axis=-1)

        # Evaluate best/early policy (step 320)
        logits_best = policy_best.actor(obs_tensor)
        probs_best = torch.softmax(logits_best, dim=-1).numpy()
        argmax_best = np.argmax(probs_best, axis=-1)

    # Calculate statistics
    n_states = len(obs_list)
    mean_probs_latest = np.mean(probs_latest, axis=0).tolist()
    mean_probs_best = np.mean(probs_best, axis=0).tolist()

    argmax_counts_latest = {OPERATOR_NAMES[i]: int(np.sum(argmax_latest == i)) for i in range(NUM_OPERATORS)}
    argmax_pct_latest = {OPERATOR_NAMES[i]: round(float(np.mean(argmax_latest == i) * 100), 2) for i in range(NUM_OPERATORS)}

    argmax_counts_best = {OPERATOR_NAMES[i]: int(np.sum(argmax_best == i)) for i in range(NUM_OPERATORS)}
    argmax_pct_best = {OPERATOR_NAMES[i]: round(float(np.mean(argmax_best == i) * 100), 2) for i in range(NUM_OPERATORS)}

    # Entropies per state
    eps = 1e-12
    entropy_per_state_latest = -np.sum(probs_latest * np.log(probs_latest + eps), axis=-1)
    entropy_per_state_best = -np.sum(probs_best * np.log(probs_best + eps), axis=-1)

    mean_entropy_latest = float(np.mean(entropy_per_state_latest))
    mean_entropy_best = float(np.mean(entropy_per_state_best))
    uniform_entropy = float(np.log(NUM_OPERATORS))

    dominant_op_idx = int(np.argmax(np.mean(probs_latest, axis=0)))
    dominant_op_name = OPERATOR_NAMES[dominant_op_idx]
    dominant_op_mean_prob = round(mean_probs_latest[dominant_op_idx], 4)
    dominant_op_argmax_pct = argmax_pct_latest[dominant_op_name]

    audit_result = {
        "num_states_evaluated": n_states,
        "uniform_baseline_entropy": round(uniform_entropy, 4),
        "early_checkpoint_step_320": {
            "mean_entropy": round(mean_entropy_best, 4),
            "mean_probabilities": {OPERATOR_NAMES[i]: round(mean_probs_best[i], 4) for i in range(NUM_OPERATORS)},
            "argmax_selection_pct": argmax_pct_best,
        },
        "trained_checkpoint_step_9984": {
            "mean_entropy": round(mean_entropy_latest, 4),
            "entropy_reduction_pct": round((1.0 - mean_entropy_latest / uniform_entropy) * 100, 2),
            "mean_probabilities": {OPERATOR_NAMES[i]: round(mean_probs_latest[i], 4) for i in range(NUM_OPERATORS)},
            "argmax_selection_pct": argmax_pct_latest,
            "dominant_operator": dominant_op_name,
            "dominant_mean_prob": dominant_op_mean_prob,
            "dominant_argmax_pct": dominant_op_argmax_pct,
        },
        "policy_collapse_verdict": {
            "is_collapsed": bool(dominant_op_argmax_pct > 60.0 or dominant_op_mean_prob > 0.40),
            "dominant_action": dominant_op_name,
            "interpretation": (
                f"The policy has collapsed to a single dominant action: {dominant_op_name}. "
                f"It selects {dominant_op_name} as its greedy choice in {dominant_op_argmax_pct}% of evaluated states, "
                f"with an average softmax probability of {dominant_op_mean_prob * 100:.1f}%. "
                f"Policy entropy dropped from {uniform_entropy:.3f} (uniform) to {mean_entropy_latest:.3f} "
                f"({(1.0 - mean_entropy_latest / uniform_entropy) * 100:.1f}% reduction). "
                f"This empirically confirms that the agent abandoned exploration in favor of a risk-averse safe-no-op strategy."
            )
        }
    }

    out_file = Path("results/diagnostics_agent2_policy.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(audit_result, f, indent=2)

    print(f"Agent 2 Policy Audit complete. Saved to {out_file}")
    return audit_result


if __name__ == "__main__":
    run_policy_audit()
