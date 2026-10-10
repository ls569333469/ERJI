import os,sys,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
mode=sys.argv[1] if len(sys.argv)>1 else 'dev'
env={**os.environ,'ERJI_API_ORIGIN':'http://127.0.0.1:18803','ERJI_NEXT_DIST_DIR':'.next-pd2-'+mode}
args=['C:/Program Files/nodejs/node.exe',str(ROOT/'node_modules/next/dist/bin/next'),mode,str(ROOT/'apps/web'),'--webpack']
if mode=='dev':args+=['--hostname','127.0.0.1','--port','18802']
raise SystemExit(subprocess.call(args,cwd=ROOT,env=env,creationflags=subprocess.CREATE_NO_WINDOW))
