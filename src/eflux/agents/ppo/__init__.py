"""PPO agent — a custom torch actor-critic (online_ppo) warm-started by behavior
cloning (bc), trained on the structured-action Gymnasium env (primitive_env) and
fine-tuned online in the live simulator."""

from __future__ import annotations
