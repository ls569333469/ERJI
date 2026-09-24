import io,json,zipfile,httpx,ssl
from pathlib import Path
with httpx.Client(verify=ssl.create_default_context(),timeout=30,follow_redirects=True) as c:
 r=c.get('https://codeload.github.com/binance/binance-skills-hub/zip/refs/heads/main');r.raise_for_status()
 z=zipfile.ZipFile(io.BytesIO(r.content))
 files=[p for p in z.namelist() if p.endswith(('.md','.mjs'))]
 print(json.dumps({'downloaded_documents':len(files)}))
 for name in files:
  if any(k in name.lower() for k in ('rank','token-info','token-search','meme','token-audit')):
   out=Path('docs/api/binance-skills')/('/'.join(name.split('/')[1:]))
   out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(z.read(name))
