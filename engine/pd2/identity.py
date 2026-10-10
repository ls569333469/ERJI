"""Reviewed ticker/slug pairs plus exact chain-contract matches, never fuzzy name joins."""
CHAIN_SLUGS={'ethereum':'1','bnb':'56','bnb-smart-chain':'56','base':'8453','solana':'501','arbitrum':'42161'}


def contract_key(chain,address):
    if not chain or not address:return None
    chain=str(chain).strip();address=str(address).strip()
    chain={'CT_501':'501','solana':'501','ethereum':'1','bsc':'56','bnb':'56','base':'8453','arbitrum':'42161'}.get(chain,chain)
    if chain in ('1','56','8453','42161'):address=address.lower()
    return chain,address


def exact_contract_links(instruments,rows):
    """Only the provider's exact chain/address can extend the reviewed registry."""
    candidates={}
    for row in rows:
        platform=row.get('platform') or {}
        key=contract_key(CHAIN_SLUGS.get(platform.get('slug')),platform.get('token_address'))
        if key and row.get('is_active',1)==1 and row.get('id') is not None:
            candidates.setdefault(key,{})[str(row['id'])]=row
    result=[]
    for inst in instruments:
        key=contract_key(inst.spec.get('chain_id'),inst.spec.get('contract_address'))
        choices=candidates.get(key,{})
        if len(choices)!=1:continue
        ident,row=next(iter(choices.items()))
        result.append({'asset_id':inst.asset_id,'external_id':ident,'evidence':{
            'method':'exact_chain_contract','chain_id':key[0],'contract_address':key[1],
            'provider_symbol':row.get('symbol'),'source':'CMC /v1/cryptocurrency/map','multiplier':'1'}})
    return result
