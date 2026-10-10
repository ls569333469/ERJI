"""Run a local validation module with the same environment as the local launcher."""
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def prepare():
    os.chdir(ROOT)
    sys.path.insert(0,str(ROOT))
    for path in (ROOT.parents[2]/'.env', ROOT/'.env'):
        if path.exists():
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key,value=line.split('=',1)
                    os.environ[key.strip()]=value.strip().strip('"').strip("'")
    # Never let diagnostics hang for minutes on a stopped local cluster.
    os.environ.setdefault('PGCONNECT_TIMEOUT','5')

if __name__=='__main__':
    prepare()
    module=sys.argv[1];sys.argv=sys.argv[1:]
    runpy.run_module(module,run_name='__main__')
