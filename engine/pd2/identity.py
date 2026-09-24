"""Reviewed ticker/slug pairs plus exact chain-contract matches, never fuzzy name joins."""
from sqlalchemy import select
from engine.db import Session
from engine.models import IdentityLink,Instrument
from engine.adapters.base import now_ms
REVIEWED={
 'BTC':'bitcoin','ETH':'ethereum','SOL':'solana','HYPE':'hyperliquid','ASTER':'aster','RAVE':'rave-dao',
 'DOGE':'dogecoin','PEPE':'pepe','BNB':'bnb','XRP':'xrp','ADA':'cardano','TRX':'tron','LINK':'chainlink',
 'AVAX':'avalanche','SUI':'sui','TON':'toncoin','DOT':'polkadot-new','LTC':'litecoin','BCH':'bitcoin-cash',
 'UNI':'uniswap','AAVE':'aave','NEAR':'near-protocol','ICP':'internet-computer','INJ':'injective','ENA':'ethena',
 'OP':'optimism-ethereum','ARB':'arbitrum','CRV':'curve-dao-token','DASH':'dash','ZEC':'zcash','XLM':'stellar',
 'ETC':'ethereum-classic','FIL':'filecoin','APT':'aptos','SEI':'sei','TIA':'celestia','WLD':'worldcoin-org',
 'JUP':'jupiter-ag','JTO':'jito','BONK':'bonk1','WIF':'dogwifhat','FLOKI':'floki-inu','SHIB':'shiba-inu',
 'PENDLE':'pendle','FET':'artificial-superintelligence-alliance','RENDER':'render','TAO':'bittensor',
 'PYTH':'pyth-network','RUNE':'thorchain','ONDO':'ondo-finance','TRUMP':'official-trump','XPL':'plasma',
 'PIPPIN':'pippin','TUT':'tutorial','MYX':'myx-finance','COAI':'chainopera-ai','SIREN':'siren',
 'USDT':'tether','USDC':'usd-coin'
}
def save_map(document):
    rows=document.get('data') or [];updated=0;conflicts=[]
    if not isinstance(rows,list):return {'updated':0,'status':'invalid_payload'}
    with Session.begin() as s:
        assets=set(s.scalars(select(Instrument.asset_id)))
        for symbol,slug in REVIEWED.items():
            matched=[r for r in rows if r.get('symbol')==symbol and r.get('slug')==slug and r.get('is_active',1)==1]
            if len(matched)!=1:conflicts.append(symbol);continue
            row=matched[0]
            for asset in [a for a in (symbol,'aster:'+symbol,'hyperliquid:'+symbol) if a in assets]:
                old=s.get(IdentityLink,(asset,'cmc'))
                if old and old.status=='verified' and old.external_id!=str(row['id']):conflicts.append(asset);continue
                s.merge(IdentityLink(asset_id=asset,provider='cmc',external_id=str(row['id']),status='verified',evidence={'method':'reviewed_symbol_and_exact_slug','symbol':symbol,'slug':slug,'name':row.get('name'),'source':'CMC /v1/cryptocurrency/map'},known_at=now_ms()))
                updated+=1
    return {'updated':updated,'unresolved':conflicts,'scope':'reviewed_identity_registry'}
