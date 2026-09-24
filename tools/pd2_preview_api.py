import pd2_env
import asyncio,os
from pathlib import Path
os.environ['ERJI_WEB_ORIGIN']='http://127.0.0.1:18802'
os.environ['ERJI_API_ORIGIN']='http://127.0.0.1:18803'
import uvicorn
async def main():
    stop=Path(__file__).resolve().parents[1]/'.runtime/pd2-preview-api.stop'
    stop.unlink(missing_ok=True)
    server=uvicorn.Server(uvicorn.Config('services.api.main:app',host='127.0.0.1',port=18803,log_level='warning'))
    async def watch():
        while not server.should_exit:
            if stop.exists():server.should_exit=True;break
            await asyncio.sleep(1)
    await asyncio.gather(server.serve(),watch())
asyncio.run(main())
