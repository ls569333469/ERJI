"""Materialize only the fixed chart directory. Runtime never downloads assets."""
import concurrent.futures, hashlib, json, urllib.request, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REV="4338800674706e4cbd88e65b873cc08d557dfbf0"
PREFIX="public/charting_library/"
DEST=ROOT/"apps/web/public/charting_library"
HEADERS={"User-Agent":"ERJI-setup/0.1"}
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url,headers=HEADERS),timeout=40) as r: return r.read()
def git_hash(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
def main():
    repo=ROOT/".runtime/chart-repo"
    if not (repo/".git").exists():
        repo.mkdir(parents=True,exist_ok=True)
        subprocess.run(["git","init",str(repo)],check=True,capture_output=True)
        subprocess.run(["git","-C",str(repo),"fetch","--depth=1","--filter=blob:none","https://github.com/earnadvise/Arc-Terminal.git",REV],check=True,capture_output=True)
    listing=subprocess.run(["git","-C",str(repo),"ls-tree","-r",REV,"--",PREFIX],check=True,capture_output=True,text=True).stdout
    nodes=[]
    for line in listing.splitlines():
        meta,path=line.split("\t",1);mode,kind,sha=meta.split()
        if kind=="blob":nodes.append({"path":path,"mode":mode,"sha":sha})
    if not nodes: raise SystemExit("No chart assets in fixed commit")
    def download(node):
        rel=node["path"][len(PREFIX):]
        target=(DEST/rel).resolve()
        if not target.is_relative_to(DEST.resolve()) or node.get("mode")=="120000": raise ValueError("Invalid asset")
        data=target.read_bytes() if target.exists() else b""
        if git_hash(data)!=node["sha"]:
            old=ROOT/"preview/charting_library"/rel
            data=old.read_bytes() if old.exists() else b""
            if git_hash(data)!=node["sha"]:
                data=get(f"https://raw.githubusercontent.com/earnadvise/Arc-Terminal/{REV}/{node['path']}")
            if git_hash(data)!=node["sha"]: raise ValueError("Git blob hash mismatch: "+rel)
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        return {"path":rel,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"git_blob":node["sha"]}
    rows=[];failures=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        pending={pool.submit(download,n):n for n in nodes}
        for task in concurrent.futures.as_completed(pending):
            try: rows.append(task.result())
            except Exception as e: failures.append({"path":pending[task]["path"],"error":type(e).__name__})
    report={"commit":REV,"complete":not failures,"count":len(rows),"total":len(nodes),"files":sorted(rows,key=lambda x:x["path"]),"failures":failures}
    out=ROOT/"chart-assets-manifest.json";out.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="files"}))
    if failures: raise SystemExit(1)
if __name__=="__main__": main()
