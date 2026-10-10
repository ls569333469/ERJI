"""Parse public catalog data, without evaluating webpage JavaScript."""
import json,re
from html.parser import HTMLParser
class Scripts(HTMLParser):
    def __init__(self):super().__init__();self.active=False;self.rows=[]
    def handle_starttag(self,tag,attrs):self.active=tag=='script'
    def handle_endtag(self,tag):
        if tag=='script':self.active=False
    def handle_data(self,text):
        if self.active:self.rows.append(text)
def map_symbols(page):
    parser=Scripts();parser.feed(page);candidates=[]
    decoder=json.JSONDecoder()
    for script in parser.rows:
        for hit in re.finditer(r'self\.__next_f\.push\(',script):
            try:args,_=decoder.raw_decode(script[hit.end():])
            except (ValueError,TypeError):continue
            for value in args if isinstance(args,list) else []:
                if not isinstance(value,str):continue
                for field in re.finditer(r'"symbols"\s*:\s*',value):
                    try:symbols,_=decoder.raw_decode(value[field.end():])
                    except (ValueError,TypeError):continue
                    if isinstance(symbols,list) and symbols and all(isinstance(x,str) and re.fullmatch(r'[A-Z0-9_:-]{1,80}',x) for x in symbols):candidates.append(symbols)
    if not candidates:raise ValueError('catalog_schema_changed')
    # UI model symbols array is the largest explicit ticker list, not the quote board.
    return list(dict.fromkeys(max(candidates,key=len)))
