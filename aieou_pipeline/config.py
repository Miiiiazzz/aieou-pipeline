"""Load config.toml and fill in defaults."""
from __future__ import annotations

import copy
import pathlib
import tomllib

DEFAULTS = {
    "input": {
        "path": "drug_bias_prompts.xlsx",
        "sheet": "",
        "id_column": "Case",
        "prompt_column": "user_prompt",
    },
    "sample": {
        "mode": "baseline",  # baseline | stratified | all
        "pair_columns": ["disease", "drug_or_class"],
        "demographic_columns": [
            "age", "race_ethnicity", "sex", "lgbtq_identity", "income_status",
        ],
        "n_per_pair": 5,
        "seed": 42,
        "limit": 0,
        "filter": {},
    },
    "run": {
        "data_dir": "data",
        "repeats": 1,
        "workers": 4,
        "max_attempts": 4,
        "timeout_s": 120,
        "save_html": True,
    },
    "providers": {},
    "surfaces": {},
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: str, dry_run: bool = False) -> dict:
    cfg_path = pathlib.Path(path).resolve()
    with open(cfg_path, "rb") as fh:
        cfg = _merge(DEFAULTS, tomllib.load(fh))
    base = cfg_path.parent

    cfg["input"]["path"] = str((base / cfg["input"]["path"]).resolve())
    data_dir = (base / cfg["run"]["data_dir"]).resolve()
    if dry_run:
        # Dry runs never touch real data: they get their own folder.
        data_dir = data_dir.with_name(data_dir.name + "_dryrun")
    cfg["run"]["data_dir"] = str(data_dir)
    cfg["dry_run"] = dry_run

    if not cfg["surfaces"]:
        raise SystemExit("config: define at least one [surfaces.<name>] section")
    for name, surface in cfg["surfaces"].items():
        for required in ("provider", "engine"):
            if required not in surface:
                raise SystemExit(f"config: surfaces.{name} is missing '{required}'")
        surface["name"] = name
        surface.setdefault("params", {})
        surface.setdefault("price_per_1000", 0.0)
    return cfg
