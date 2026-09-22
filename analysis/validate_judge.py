#!/usr/bin/env python3
"""CLI do contrato do `judge.json` — casca fina sobre o modelo de `judgement`.

python analysis/validate_judge.py quality/results/<run_id>/judge.json
python analysis/validate_judge.py --emit-schema > analysis/judge.schema.json
"""

from __future__ import annotations

import sys

from json_contract import validate_cli
from judgement import Judgement

if __name__ == "__main__":
    sys.exit(validate_cli(artifact="judge.json", model=Judgement))
