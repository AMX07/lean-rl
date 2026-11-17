"""Run in a Prime RL v0.6.0 environment to validate the complete trainer schema."""
from pathlib import Path
import tomllib

from prime_rl.configs.rl import RLConfig

root = Path(__file__).resolve().parents[1]
for path in sorted((root / "configs/rl").glob("*.toml")):
    config = RLConfig.model_validate(tomllib.loads(path.read_text()))
    print(path.name, "validated", config.model.name)
