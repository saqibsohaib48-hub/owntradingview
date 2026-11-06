"""Write manifest.json: the list of files the web page loads into in-browser Python.

GitHub Pages can't list folders, so the page reads this list instead.
The GitHub Pages workflow runs this automatically on every push; run it yourself with
    python tools/build_manifest.py
if you host the site some other way.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ["app/**/*.py", "indicators/*.py", "indicators/*.pine",
            "strategies/*.py", "strategies/*.pine", "data/*.csv"]
EXCLUDE = {"app/server.py"}  # needs FastAPI, not used in the browser


def build() -> dict:
    files = sorted({p.relative_to(ROOT).as_posix() for pat in PATTERNS for p in ROOT.glob(pat)
                    if "__pycache__" not in p.parts and not p.name.startswith("_tmp_")} - EXCLUDE)
    return {"files": files}


if __name__ == "__main__":
    m = build()
    (ROOT / "manifest.json").write_text(json.dumps(m, indent=1) + "\n")
    print(f"manifest.json: {len(m['files'])} files")
