import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/"chart-assets-manifest.json").read_text())
assert manifest["complete"],"Chart download incomplete"
for row in manifest["files"]:
    file=root/"apps/web/public/charting_library"/row["path"]
    assert hashlib.sha256(file.read_bytes()).hexdigest()==row["sha256"],row["path"]
print(f"Verified {len(manifest['files'])} chart files against fixed manifest")

