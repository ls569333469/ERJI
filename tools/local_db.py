"""Isolated local cluster using already installed PostgreSQL binaries."""
import argparse, os, secrets, socket, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
DATA = RUNTIME / "postgres"
PORT = 55432
BIN = Path(os.environ.get("ERJI_PG_BIN", r"D:\PostgreSQL\bin"))
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

def run(name, args, **kwargs):
    exe = BIN / (name + (".exe" if os.name == "nt" else ""))
    return subprocess.run([str(exe), *args], creationflags=FLAGS, **kwargs)

def main():
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["start","stop","status"]);args=parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    if args.action != "start":
        return run("pg_ctl", ["-D",str(DATA),args.action, *(["-m","fast"] if args.action=="stop" else [])]).returncode
    if not (BIN / "initdb.exe").exists() and not (BIN / "initdb").exists():
        raise SystemExit("Set ERJI_PG_BIN to the PostgreSQL bin directory, or use Docker Compose.")
    if (DATA / "PG_VERSION").exists():
        if run("pg_ctl",["-D",str(DATA),"status"],capture_output=True).returncode == 0:
            print("ERJI local PostgreSQL already running");return 0
    else:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1",PORT)) == 0:
                raise SystemExit("Port 55432 is already in use; no existing database was changed.")
        if (ROOT/".env").exists():
            raise SystemExit("Existing .env detected without local cluster. Configure explicitly; file not overwritten.")
        password=secrets.token_urlsafe(32)
        pwfile=RUNTIME/"init-password.tmp"
        pwfile.write_text(password,encoding="utf-8")
        try:
            result=run("initdb",["-D",str(DATA),"-U","erji","--encoding=UTF8","--locale=C","--auth=scram-sha-256","--pwfile="+str(pwfile)],capture_output=True)
            if result.returncode: raise SystemExit(result.stderr.decode(errors="replace"))
        finally:
            pwfile.unlink(missing_ok=True)
        with (DATA/"postgresql.conf").open("a",encoding="utf-8") as f:
            f.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\n")
        (ROOT/".env").write_text(
            "ERJI_DATABASE_URL=postgresql+psycopg://erji:"+password+"@127.0.0.1:55432/erji\n"
            "ERJI_MODE=research_live\nERJI_WEB_ORIGIN=http://127.0.0.1:18770\n"
            "ERJI_API_ORIGIN=http://127.0.0.1:18771\nERJI_SYMBOLS=BTC,ETH,SOL,HYPE\n",encoding="utf-8")
    log=(RUNTIME/"postgres.log").open("ab")
    executable=BIN/("postgres.exe" if os.name=="nt" else "postgres")
    process=subprocess.Popen([str(executable),"-D",str(DATA)],stdout=log,stderr=log,creationflags=FLAGS)
    log.close()
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url
    import psycopg
    url=make_url(dotenv_values(ROOT/".env")["ERJI_DATABASE_URL"])
    conn=None
    for _ in range(30):
        try:
            conn=psycopg.connect(host=url.host,port=url.port,user=url.username,password=url.password,dbname="postgres",autocommit=True)
            break
        except psycopg.OperationalError:
            if process.poll() is not None: raise SystemExit("PostgreSQL exited. See .runtime/postgres.log")
            time.sleep(0.5)
    if conn is None: raise SystemExit("PostgreSQL did not become ready. See .runtime/postgres.log")
    with conn:
        if not conn.execute("select 1 from pg_database where datname='erji'").fetchone():
            conn.execute('CREATE DATABASE erji')
    print("ERJI local PostgreSQL ready on 127.0.0.1:55432; existing service untouched")
    return 0

if __name__=="__main__": sys.exit(main())
