"""Actor-Critic Policy Architecture for PPO-guided ALNS (Subagent 3).

Implements dual MLP trunks for policy logits (8 discrete actions) and state value estimation,
with orthogonal initialization, Gaussian entropy regularizer, and CUDA acceleration.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Tuple

try:
    import torch
    import torch.nn as nn
    from torch.distributions.categorical import Categorical
except ImportError:
    # Fallback placeholder while torch wheel finishes installing
    torch = None  # type: ignore
    nn = None  # type: ignore
    Categorical = None  # type: ignore

from rl.observations import OBSERVATION_DIM
from resplan_router.optimizers.alns_operators import NUM_OPERATORS


def layer_init(layer: Any, std: float = 1.4142, bias_const: float = 0.0) -> Any:
    """Apply orthogonal weight initialization."""
    if nn is None:
        return layer
    nn.init.orthogonal_(layer.weight, std)
    nn.init.constant_(layer.bias, bias_const)
    return layer


class ActorCriticPolicy(nn.Module if nn is not None else object):  # type: ignore
    """Dual-head Actor-Critic network for ALNS operator selection."""

    def __init__(
        self,
        obs_dim: int = OBSERVATION_DIM,
        action_dim: int = NUM_OPERATORS,
        hidden_dim: int = 128,
    ) -> None:
        if nn is None:
            raise ImportError("PyTorch is not yet installed.")
        super().__init__()
        self.obs_dim = obs_dim
        self.action_dim = action_dim

        # Separate Actor trunk to avoid value gradient interference
        self.actor = nn.Sequential(
            layer_init(nn.Linear(obs_dim, hidden_dim)),
            nn.Tanh(),
            layer_init(nn.Linear(hidden_dim, hidden_dim)),
            nn.Tanh(),
            layer_init(nn.Linear(hidden_dim, action_dim), std=0.01),
        )

        # Separate Critic trunk
        self.critic = nn.Sequential(
            layer_init(nn.Linear(obs_dim, hidden_dim)),
            nn.Tanh(),
            layer_init(nn.Linear(hidden_dim, hidden_dim)),
            nn.Tanh(),
            layer_init(nn.Linear(hidden_dim, 1), std=1.0),
        )

    def get_value(self, x: torch.Tensor) -> torch.Tensor:
        """Compute state value V(s)."""
        return self.critic(x).squeeze(-1)

    def get_action_and_value(
        self,
        x: torch.Tensor,
        action: Optional[torch.Tensor] = None,
        deterministic: bool = False,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """Compute action, log_prob, entropy, and value estimate."""
        logits = self.actor(x)
        probs = Categorical(logits=logits)

        if action is None:
            if deterministic:
                action = torch.argmax(logits, dim=-1)
            else:
                action = probs.sample()

        return action, probs.log_prob(action), probs.entropy(), self.critic(x).squeeze(-1)

    def get_action(self, x: torch.Tensor, deterministic: bool = False) -> int:
        """Single-step action inference for deployment."""
        with torch.no_grad():
            logits = self.actor(x)
            if deterministic:
                action = torch.argmax(logits, dim=-1)
            else:
                probs = Categorical(logits=logits)
                action = probs.sample()
            return int(action.item())

    def save(self, path: Path | str) -> None:
        """Save model weights to disk."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        torch.save(self.state_dict(), p)

    @classmethod
    def load(cls, path: Path | str, device: str = "cpu") -> ActorCriticPolicy:
        """Load model weights from disk."""
        model = cls()
        model.load_state_dict(torch.load(path, map_location=device))
        model.to(device)
        model.eval()
        return model
