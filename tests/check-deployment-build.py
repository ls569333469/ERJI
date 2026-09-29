"""Read-only check of the actual Next production output for /erji routing."""
import json,re
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'apps/web/.next-erji-deploy'
routes=json.loads((root/'routes-manifest.json').read_text())
assert routes['basePath']=='/erji'
assert routes['redirects']==[], 'Avoid trailing-slash redirect loop with Nginx /erji -> /erji/'
rewrite=routes['rewrites']['afterFiles'][0]
assert rewrite['source']=='/erji/v1/:path*'
assert rewrite['destination'].endswith('/v1/:path*') and '/erji/v1/' not in rewrite['destination']
assert json.loads((root/'server/app/index.meta').read_text())['headers']['location']=='/erji/signals'
for name in ('signals','login','markets'):
 html=(root/'server/app'/f'{name}.html').read_text(encoding='utf-8')
 assert '/erji/_next/' in html
 assert 'href="/erji/signals"' in html
 for resource in re.findall(r'(?:src|href)="(/erji/_next/[^"?]+)',html):
  relative=resource.removeprefix('/erji/_next/')
  assert (root/relative).is_file(),resource
print('Production basePath, rewrite, navigation, redirect and static assets passed')
