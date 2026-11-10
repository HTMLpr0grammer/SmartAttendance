"""Build SmartAttendance-upload.zip for PythonAnywhere's Files tab.

Run from the project folder:   python deploy\\make_upload_zip.py
The zip is written next to the project folder (one level up) and leaves out your virtualenv,
local database, secrets, caches, the docs folder and any .exe tools. Add --include-db to also ship your local db.sqlite3.
"""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "venv", "env", "__pycache__", ".git", ".idea", ".vscode", "staticfiles", "node_modules", "docs"}
SKIP_FILES = {".env", "db.sqlite3", "db.sqlite3-journal"}


def main():
    include_db = "--include-db" in sys.argv
    out = ROOT.parent / f"{ROOT.name}-upload.zip"
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT)
            if any(part in SKIP_DIRS for part in rel.parts) or not path.is_file():
                continue
            if path.suffix in {".pyc", ".zip", ".exe"}:
                continue
            if rel.name in SKIP_FILES and not (include_db and rel.name == "db.sqlite3"):
                continue
            # Forward slashes so the paths unzip correctly on Linux.
            zf.write(path, f"{ROOT.name}/{rel.as_posix()}")
            count += 1
    print(f"Wrote {out} ({count} files, {out.stat().st_size / 1024:.0f} KB)")
    if include_db:
        print("Included db.sqlite3 – the live site will start with your local data.")


if __name__ == "__main__":
    main()
