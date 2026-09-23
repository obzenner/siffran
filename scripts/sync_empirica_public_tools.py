#!/usr/bin/env python3
"""Regenerate flat public tool schemas from the canonical runtime contract projection."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/empirica'))
from adapters.public_tools import _PUBLIC_SCHEMAS  # noqa: E402

path = ROOT / 'contracts/empirica/v2/public-tools.json'
path.write_text(json.dumps({'protocol': 'empirica/v2', 'schemas': _PUBLIC_SCHEMAS}, indent=2) + '\n')
print('regenerated flat public tools from vendored canonical contracts; run make vendor-contracts')
