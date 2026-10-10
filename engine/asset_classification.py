"""Display-only product identity from an archived official Binance catalog.

Never infer a stock from a ticker suffix or use its underlying as an asset alias.
This module does not change universe admission, collection or trading rules.
"""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def catalog():
    try:
        return json.loads(Path(__file__).with_name('asset_classification_catalog.json').read_text('utf8'))
    except (OSError, ValueError):
        return {'assets': {}, 'source': None, 'checked_at': None}


def classification(instrument_id, asset_id, spec=None):
    spec=spec or {}
    result={'asset_class':'unknown','asset_class_label':'分类待核验',
            'asset_class_source':None,'asset_class_as_of':None,
            'asset_name':None,'underlying_symbol':None}
    # Native instrument metadata is stronger than a cross-product name match.
    if spec.get('underlyingType')=='EQUITY':
        return {**result,'asset_class':'equity_derivative','asset_class_label':'股票合约',
                'asset_class_source':'Binance futures exchangeInfo'}
    if spec.get('underlyingType')=='COIN':
        return {**result,'asset_class':'crypto','asset_class_label':'加密资产',
                'asset_class_source':'Binance futures exchangeInfo'}
    if not instrument_id.startswith(('binance:', 'binance_spot:')):
        return result
    data=catalog();item=data['assets'].get(spec.get('base_asset') or asset_id)
    if item:
        result.update(asset_class_source=data.get('source'),asset_class_as_of=data.get('checked_at'),
                      asset_name=item.get('name'),underlying_symbol=item.get('underlying'))
        if 'bStocks' in item.get('tags',[]):
            etf='ETF' in (item.get('name') or '') or item.get('name')=='Invesco QQQ Trust (bStocks)'
            result.update(asset_class='equity_etf_token' if etf else 'equity_token',
                          asset_class_label='股票ETF代币' if etf else '股票代币')
        elif item.get('fiat'):
            result.update(asset_class='fiat',asset_class_label='法币')
        else:
            result.update(asset_class='crypto',asset_class_label='加密资产')
    elif instrument_id.startswith('binance:') and spec.get('source')=='exchangeInfo':
        # Existing native futures catalog was admitted by the strict COIN filter.
        result.update(asset_class='crypto',asset_class_label='加密资产',
                      asset_class_source='Binance crypto futures catalog')
    return result
