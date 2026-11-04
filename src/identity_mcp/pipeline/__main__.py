"""Command line: ``uv run python -m identity_mcp.pipeline "How many users are terminated?"``"""

import asyncio
import json
import sys

from ..core.logs import configure
from .engine import ask

configure()
if len(sys.argv) < 2:
    sys.exit('usage: python -m identity_mcp.pipeline "your question"')
print(json.dumps(asyncio.run(ask(" ".join(sys.argv[1:]))), indent=2, default=str))
