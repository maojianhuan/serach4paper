#!/usr/bin/env python3
"""Package the standalone plugin; Python is only a build tool, never a runtime dependency."""
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent
version = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["version"]
output = root.parent / "dist" / f"search4paper-{version}.xpi"
output.parent.mkdir(exist_ok=True)
files = [root / "manifest.json", root / "bootstrap.js", *sorted((root / "content").rglob("*"))]
with ZipFile(output, "w", ZIP_DEFLATED) as archive:
    for path in files:
        if path.is_file():
            archive.write(path, path.relative_to(root))
print(output)
