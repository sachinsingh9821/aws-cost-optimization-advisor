"""Cross-platform task runner (works on Windows without `make`).

Usage: python tasks.py <command>
  data      regenerate synthetic data and ground-truth labels (deterministic)
  lint      ruff check + format check
  test      pytest with coverage (fails under 85%)
  evaluate  generate reports/evaluation_report.md
  app       launch the Streamlit dashboard
  check     lint + test + evaluate (what CI runs)
"""

from __future__ import annotations

import subprocess
import sys

PY = sys.executable

COMMANDS: dict[str, list[list[str]]] = {
    "data": [[PY, "-m", "src.data.generator"], [PY, "-m", "src.evaluation.labels"]],
    "lint": [[PY, "-m", "ruff", "check", "."], [PY, "-m", "ruff", "format", "--check", "."]],
    "test": [[PY, "-m", "pytest", "--cov", "--cov-report=term", "--cov-fail-under=85"]],
    "evaluate": [[PY, "-m", "src.evaluation.report"]],
    "app": [[PY, "-m", "streamlit", "run", "app.py"]],
}
COMMANDS["check"] = COMMANDS["lint"] + COMMANDS["test"] + COMMANDS["evaluate"]


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    for cmd in COMMANDS[argv[0]]:
        print("$", " ".join(cmd[1:] if cmd[0] == PY else cmd), flush=True)
        code = subprocess.call(cmd)
        if code:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
