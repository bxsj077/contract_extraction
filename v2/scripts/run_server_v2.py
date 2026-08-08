from __future__ import annotations

import sys
from pathlib import Path

import uvicorn


V2_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = V2_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


if __name__ == "__main__":
    uvicorn.run(
        "contract_extraction_v2.api:app",
        host="127.0.0.1",
        port=8001,
        reload=False,
    )
