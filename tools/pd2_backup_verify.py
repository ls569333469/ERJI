"""Back up a read-only source and restore into a NEW disposable verification database.
No existing database/schema is dropped or overwritten. Passwords never enter argv/logs.
"""
import os,sys,json,subprocess,time,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.config import Settings
from sqlalchemy import create_engine,text,inspect
from sqlalchemy.engine import make_url
root=Path(__file__).resolve().parents[1]
source=make_url(Settings(_env_file='D:/Axiangmu/erji/.env').database_url)
name='erji_pd2_restore_'+str(int(time.time()))
bin=Path('D:/PostgreSQL/bin');dest=root/'.runtime'/'backups';dest.mkdir(parents=True,exist_ok=True)
backup=dest/(name+'.dump')
env={**os.environ,'PGPASSWORD':source.password or ''}
args=['-h',source.host or '127.0.0.1','-p',str(source.port or 5432),'-U',source.username]
def run(exe,extra):
    p=subprocess.run([str(bin/exe),*args,*extra],env=env,capture_output=True,text=True,timeout=180)
    if p.returncode:raise RuntimeError(exe+' failed: '+p.stderr[:500].replace(source.password or '<none>','[REDACTED]'))
admin=create_engine(source,isolation_level='AUTOCOMMIT')
with admin.connect() as c:c.execute(text('CREATE DATABASE '+name))
run('pg_dump.exe',['-d',source.database,'-Fc','-n','erji_pd2_20260924','-f',str(backup)])
run('pg_restore.exe',['-d',name,'--no-owner','--no-privileges','--exit-on-error',str(backup)])
src=create_engine(source.update_query_dict({'options':'-csearch_path=erji_pd2_20260924'}))
dst=create_engine(source.set(database=name).update_query_dict({'options':'-csearch_path=erji_pd2_20260924'}))
counts={}
with src.connect() as a,dst.connect() as b:
    for table in inspect(a).get_table_names(schema='erji_pd2_20260924'):
        counts[table]=[a.execute(text('SELECT count(*) FROM "'+table+'"')).scalar(),b.execute(text('SELECT count(*) FROM "'+table+'"')).scalar()]
    source_first=a.execute(text('SELECT id,first_seen_at,first_seen_price FROM campaigns ORDER BY id')).all()
    restored_first=b.execute(text('SELECT id,first_seen_at,first_seen_price FROM campaigns ORDER BY id')).all()
report={'database':name,'backup':str(backup),'sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),'table_counts':counts,'counts_equal':all(a==b for a,b in counts.values()),'first_seen_equal':source_first==restored_first,'destructive_operations':False}
(root/'docs'/'pd2-backup-restore.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(report))
