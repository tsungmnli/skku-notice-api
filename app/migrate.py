"""Apply database migrations: `python -m app.migrate`.

This is the command for the Argo CD migration Job (a Sync hook that runs before the Deployment).
The API itself never migrates on startup, so rolling out several replicas cannot race on the
schema.
"""

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    if url:
        cfg.attributes["url"] = url
    return cfg


def run(revision: str = "head", url: str | None = None) -> None:
    command.upgrade(alembic_config(url), revision)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "head")
