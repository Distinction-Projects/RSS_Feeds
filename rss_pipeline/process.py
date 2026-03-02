from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path


def run_command(command: Sequence[str], *, cwd: str | Path | None = None) -> None:
    printable = " ".join(shlex.quote(part) for part in command)
    print(f"$ {printable}", flush=True)
    run_cwd = str(cwd) if cwd is not None else None
    subprocess.run(list(command), check=True, cwd=run_cwd)
