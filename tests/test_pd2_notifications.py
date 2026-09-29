import asyncio
import httpx
import pytest
from engine.pd2.notifications import send_text,render

@pytest.mark.parametrize('status,body,expected',[(200,{'ok':True,'result':{'message_id':3}},'sent'),(429,{'ok':False,'parameters':{'retry_after':12}},'retry'),(503,{'ok':False,'error_code':503},'retry'),(503,{},'delivery_unknown'),(403,{'ok':False,'error_code':403},'failed')])
def test_transport_response_without_sending(status,body,expected):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(status,json=body))) as client:
            result=await send_text(client,'fake-local-token','local-test-only','test')
            assert result[0]==expected
            if status==429:assert result[1]['retry_after']==12
    asyncio.run(run())

def test_timeout_is_unknown_not_retry_and_no_secret_in_error():
    async def run():
        def handler(request):raise httpx.ReadTimeout('secret-url-not-to-be-logged')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            state,extra=await send_text(client,'fake-token','local-test-only','test')
            assert state=='delivery_unknown' and extra=={'reason':'ReadTimeout'}
    asyncio.run(run())

def test_snapshot_links_encoded_and_local_url_hidden():
    p={'asset_id':'a/b:币','action':{'label':'建议参与'},'evidence':{'entry_zone':['1','2']},'id':'snap/1','episode_id':'episode?1','instrument_id':'x:a&b','opportunity_plan':{}}
    assert '/assets/' not in render(p,'http://127.0.0.1:18800')
    assert '/assets/a%2Fb%3A%E5%B8%81?' in render(p,'https://example.com')
    assert 'instrument=x%3Aa%26b' in render(p,'https://example.com')
