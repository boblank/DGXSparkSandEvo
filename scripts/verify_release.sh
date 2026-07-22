#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"

"$PYTHON_BIN" -m unittest discover -s skills/evolution/tests -v
"$PYTHON_BIN" -m unittest discover -s knowledge/tests -v
"$PYTHON_BIN" -m compileall -q demo-ui skills/evolution knowledge
bash -n skills/evolution/run_helper.sh scripts/verify_release.sh
node --check demo-ui/app.js

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

for root in (Path("skills/evolution"), Path("knowledge"), Path("demo-assets")):
    for path in sorted(root.rglob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))
print("JSON validation passed")
PY

git diff --check
