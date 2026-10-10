"""Package committed source and local chart assets; no credentials or runtime data."""
import hashlib,io,json,re,subprocess,tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
tracked_archive=subprocess.check_output(['git','archive','--format=tar','HEAD'],cwd=ROOT)
files={}
with tarfile.open(fileobj=io.BytesIO(tracked_archive),mode='r:') as source:
 for member in source.getmembers():
  if member.isdir():continue
  if not member.isfile():raise SystemExit('Unsupported git file: '+member.name)
  data=source.extractfile(member).read()
  if member.name.endswith('.sh') and b'\r' in data:
   raise SystemExit('Shell archive must use LF line endings: '+member.name)
  files[member.name]=data
chart=ROOT/'apps/web/public/charting_library'
if not (chart/'charting_library.standalone.js').is_file():raise SystemExit('Chart assets missing')
for p in chart.rglob('*'):
 if p.is_symlink():raise SystemExit('Symlink in chart assets')
 if p.is_file():files[p.relative_to(ROOT).as_posix()]=p.read_bytes()
output=ROOT/'.runtime/deployment';output.mkdir(parents=True,exist_ok=True)
archive=output/('erji-'+commit[:12]+'.tar.gz')
manifest={'base_commit':commit,'source':'git archive HEAD','chart_source':'local installed chart assets with per-file hashes','files':[]}
with tarfile.open(archive,'w:gz') as tar:
 directories=sorted({parent.as_posix() for rel in files for parent in Path(rel).parents if parent.as_posix()!='.'},key=lambda p:(p.count('/'),p))
 for directory in directories:
  info=tarfile.TarInfo(directory+'/');info.type=tarfile.DIRTYPE;info.mode=0o755
  tar.addfile(info)
 for rel,data in sorted(files.items()):
  parts=Path(rel).parts
  if any(x in parts for x in ('.runtime','.venv','node_modules','.dev-deps','.git')) or (Path(rel).name.startswith('.env') and Path(rel).name!='.env.example'):raise SystemExit('Excluded path: '+rel)
  if re.search(rb'-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{35,}',data):raise SystemExit('Credential pattern: '+rel)
  info=tarfile.TarInfo(rel);info.size=len(data);info.mode=0o755 if rel.endswith('.sh') else 0o644
  tar.addfile(info,io.BytesIO(data));manifest['files'].append({'path':rel,'sha256':hashlib.sha256(data).hexdigest()})
 data=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
 info=tarfile.TarInfo('deployment-manifest.json');info.size=len(data);info.mode=0o644;tar.addfile(info,io.BytesIO(data))
sha=hashlib.sha256(archive.read_bytes()).hexdigest()
Path(str(archive)+'.sha256').write_text(sha+'  '+archive.name+'\n')
print(json.dumps({'archive':str(archive),'sha256':sha,'files':len(files),'bytes':archive.stat().st_size,'commit':commit}))
