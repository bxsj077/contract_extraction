from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("contract_extraction.api:app", host="127.0.0.1", port=8000, reload=False)
    print("1")