"""Experimental Evaluation Framework for Honeywell Post-Champion Research (Agent 5).

Provides:
- Independent experimental validation partition generator (strictly isolating frozen test split).
- Matched-budget paired evaluation protocols (identical seeds, iterations, timeouts).
- Independent oracle validation using FloorPlanChecker at 1 mm tolerance.
- Candidate optimizer wrappers for Agent 2, Agent 3, and Agent 4 research innovations.
- Paired statistical hypothesis tests (Wilcoxon signed-rank, Student's t-test) and 95% bootstrap CIs.
- Comprehensive reporting engine targeting reports/post_champion_agent5_evaluation.md.
"""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "Agent 5: Research Evaluation Lead"
