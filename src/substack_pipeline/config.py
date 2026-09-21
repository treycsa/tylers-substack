"""Load rubric.yaml and .env into one settings object."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass
class Settings:
    rubric: dict
    github_token: str | None
    anthropic_key: str | None
    anthropic_model: str
    posts_path: Path
    db_path: Path
    out_dir: Path
    tested_dir: Path
    root: Path = field(default=ROOT)

    @property
    def weights(self) -> dict[str, float]:
        return {k: float(v["weight"]) for k, v in self.rubric["signals"].items()}

    @property
    def selection(self) -> dict:
        return self.rubric["selection"]

    @property
    def labels(self) -> list[str]:
        return list(self.rubric["labels"])


def load_settings(rubric_path: Path | None = None) -> Settings:
    rubric_path = rubric_path or ROOT / "config" / "rubric.yaml"
    with open(rubric_path) as f:
        rubric = yaml.safe_load(f)

    def p(env: str, default: str) -> Path:
        v = os.getenv(env, default)
        path = Path(v)
        return path if path.is_absolute() else ROOT / path

    return Settings(
        rubric=rubric,
        github_token=os.getenv("GITHUB_TOKEN") or None,
        anthropic_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        posts_path=p("POSTS_PATH", "data/posts.jsonl"),
        db_path=p("DB_PATH", "data/pipeline.sqlite"),
        out_dir=p("OUT_DIR", "out"),
        tested_dir=p("TESTED_DIR", "tested-daily"),
    )
