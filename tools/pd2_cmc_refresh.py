import pd2_env
import asyncio,json
from pathlib import Path
from engine.pd2.providers import Supplemental
from engine.db import Session
from engine.models import IdentityLink,DataCapability
from sqlalchemy import select,func
async def main():
 a=Supplemental()
 try:await a.cmc()
 finally:await a.close()
 with Session() as s:
  report={'verified_links':s.scalar(select(func.count()).select_from(IdentityLink).where(IdentityLink.status=='verified')),'valuation_assets':s.scalar(select(func.count()).select_from(DataCapability).where(DataCapability.provider=='cmc',DataCapability.capability=='valuation')),'catalog_status':s.get(DataCapability,'cmc:identity_catalog:reviewed').status}
 print(json.dumps(report));Path('docs/pd2-cmc-expanded.json').write_text(json.dumps(report,indent=2),encoding='utf8')
asyncio.run(main())
