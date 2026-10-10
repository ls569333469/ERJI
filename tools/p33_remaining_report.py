"""Render this acceptance note and seal a scoped, hash-verified result archive."""
from pathlib import Path
import json,hashlib,zipfile,html,re

root=Path(__file__).resolve().parents[1]
directory=root/'docs/p33-validation/remaining-20261002'
note=directory/'P33_剩余工程收尾与验收.md'


def inline(value):
    value=html.escape(value)
    value=re.sub(r'`([^`]+)`',r'<code>\1</code>',value)
    value=re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',value)
    return re.sub(r'\[([^]]+)\]\(([^)]+)\)',r'<a href="\2">\1</a>',value)


def render():
    lines=note.read_text(encoding='utf-8').splitlines();out=[];table=False;listing=False
    for line in lines:
        if not line.startswith('|') and table:out.append('</tbody></table></div>');table=False
        if not line.startswith('- ') and listing:out.append('</ul>');listing=False
        if line.startswith('|'):
            cells=line.strip('|').split('|')
            if all(re.fullmatch(r'[ :\-]+',c) for c in cells):continue
            tag='td' if table else 'th'
            if not table:out.append('<div class="scroll"><table><tbody>');table=True
            out.append('<tr>'+''.join('<'+tag+'>'+inline(c.strip())+'</'+tag+'>' for c in cells)+'</tr>')
        elif line.startswith('#'):
            level=len(line)-len(line.lstrip('#'));out.append(f'<h{level}>'+inline(line[level:].strip())+f'</h{level}>')
        elif line.startswith('- '):
            if not listing:out.append('<ul>');listing=True
            out.append('<li>'+inline(line[2:])+'</li>')
        elif line.strip():out.append('<p>'+inline(line)+'</p>')
    if table:out.append('</tbody></table></div>')
    if listing:out.append('</ul>')
    out.append('<h2>可查看的实物证据</h2><p><a href="real-chart-matrix.json">七类真实事件坐标记录</a> · <a href="browser.json">受控坐标记录</a> · <a href="runtime.json">最后运行取样</a></p><figure><img src="controlled-fib-protection.png" alt="受控算法样例：斐波与三段保护坐标验收"><figcaption>受控算法样例，不是实盘行情或真实买入建议。</figcaption></figure><figure><img src="real-squeeze.png" alt="真实收缩突破研究事件"><figcaption>真实冻结研究事件，仍处于策略验证阶段。</figcaption></figure>')
    style='body{margin:0;background:#f4f6f9;color:#19314b;font:16px/1.75 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1100px;margin:auto;padding:36px}h1{font-size:28px}h2{font-size:22px;margin-top:32px}a{color:#2458c7}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;background:white}td,th{border:1px solid #dbe2ed;padding:12px;text-align:left;vertical-align:top}th{background:#edf2fa}img{max-width:100%;border:1px solid #dbe2ed}code{overflow-wrap:anywhere}li{margin:8px 0}figure{margin:24px 0}figcaption{color:#586b80}'
    target=note.with_suffix('.html')
    target.write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>P33 剩余工程收尾与验收</title><style>'+style+'</style></head><body><main>'+''.join(out)+'</main></body></html>',encoding='utf-8')


def seal():
    backup=Path((root/'.runtime/p33-remaining-backup.txt').read_text(encoding='utf-8-sig').strip())
    before=json.loads((backup/'manifest.json').read_text(encoding='utf-8-sig'))
    files=set(before)
    for folder in ('engine','services','apps/web/src','tests','tools'):
        files.update(p.relative_to(root).as_posix() for p in (root/folder).rglob('*')
            if p.is_file() and p.suffix in {'.py','.ts','.tsx','.css','.cjs','.ps1'} and '__pycache__' not in p.parts)
    files.add('migrations/versions/p33_native_tape_lookup.py')
    files.update(p.relative_to(root).as_posix() for p in directory.rglob('*') if p.is_file())
    changes=[]
    for name in sorted(files):
        file=root/name
        digest=hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None
        if digest==before.get(name):continue
        changes.append({'path':name,'status':'modified' if name in before and digest else 'deleted' if name in before else 'added',
            'before_sha256':before.get(name),'after_sha256':digest})
    with zipfile.ZipFile(backup/'result.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for change in changes:
            if change['after_sha256']:archive.writestr(change['path'],(root/change['path']).read_bytes())
    with zipfile.ZipFile(backup/'result.zip') as archive:
        assert archive.testzip() is None
        for change in changes:
            if change['after_sha256']:assert hashlib.sha256(archive.read(change['path'])).hexdigest()==change['after_sha256']
    summary={'scope':'P33 remaining local engineering changes; preserves pre-existing dirty work',
        'backup':str(backup),'archive_sha256':hashlib.sha256((backup/'result.zip').read_bytes()).hexdigest(),
        'database_changes':'Applied index-only migration p33_native_tape_lookup; ordinary local collectors continue; no market/plan/history deletion',
        'secrets_included':False,'files':changes}
    (backup/'changes.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'backup':str(backup),'changed_files':len(changes),'archive_verified':True},ensure_ascii=False))


if __name__=='__main__':render();seal()
