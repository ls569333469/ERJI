"""The production image installs the lock, not pyproject dependencies."""
from pathlib import Path
import re
import tomllib


def test_production_lock_includes_all_declared_runtime_dependencies():
    root = Path(__file__).resolve().parents[1]
    declared = tomllib.loads((root / 'pyproject.toml').read_text())['project']['dependencies']
    def name(value):
        return re.split(r'[\[<>=!~; ]', value.strip(), maxsplit=1)[0].lower().replace('_', '-')
    locked = {name(line) for line in (root / 'requirements.lock').read_text().splitlines() if line and not line.startswith('#')}
    missing = {name(item) for item in declared} - locked
    assert not missing, f'Runtime dependencies missing from production image: {sorted(missing)}'
