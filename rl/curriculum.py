"""Curriculum Learning Scheduler for PPO-ALNS (Subagent 4).

Coordinates dynamic difficulty progression from compact single-hub layouts
up to complex multi-hub layouts as training advances.
"""

from __future__ import annotations

import random
from typing import Optional
from rl.dataset import ResPlanDatasetManager


class CurriculumScheduler:
    """Manages active training difficulty stage based on global environment steps."""

    def __init__(
        self,
        dataset_manager: ResPlanDatasetManager,
        total_steps: int = 500_000,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.dataset = dataset_manager
        self.total_steps = total_steps
        self.rng = rng or random.Random(42)
        self.current_step = 0

    def step(self, n_steps: int = 1) -> None:
        """Increment global step counter."""
        self.current_step += n_steps

    @property
    def current_stage(self) -> str:
        """Get the active curriculum difficulty stage name."""
        ratio = self.current_step / max(1, self.total_steps)
        if ratio < 0.20:
            return "compact"     # Level 1: 0 - 20%
        elif ratio < 0.50:
            return "standard"    # Level 2: 20 - 50%
        elif ratio < 0.80:
            return "complex"     # Level 3: 50 - 80%
        else:
            return "all"         # Level 4: 80 - 100%

    def sample_plan_id(self) -> int:
        """Sample a plan ID matching current curriculum level."""
        return self.dataset.sample_plan_id(stratum=self.current_stage, rng=self.rng)
