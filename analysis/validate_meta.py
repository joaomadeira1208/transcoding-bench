#!/usr/bin/env python3
"""CLI do contrato do `meta.json` — casca fina sobre o modelo de `run_meta`.

python analysis/validate_meta.py runs/<run_id>/meta.json
python analysis/validate_meta.py --emit-schema > analysis/meta.schema.json
"""

from __future__ import annotations

import sys

from json_contract import validate_cli
from run_meta import RunMeta

if __name__ == "__main__":
    sys.exit(validate_cli(artifact="meta.json", model=RunMeta))
