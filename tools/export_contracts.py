import json
from pathlib import Path
from services.api.main import app
out = Path("packages/contracts/openapi.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
print("OpenAPI exported")

