from __future__ import annotations

import hashlib
import json
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(r"D:\Axiangmu\erji")
OUT = ROOT / "docs" / "api" / "official"
MANIFEST_PATH = ROOT / "docs" / "api" / "manifest.json"
OUT.mkdir(parents=True, exist_ok=True)

# source_url is the human-facing canonical page; fetch_url is the exact public
# file/page captured locally. For pages that are dynamic or protected, the
# record makes that distinction explicit.
DOCS = [
    {
        "id": "binance-spot-rest-api",
        "title": "Binance Spot REST API",
        "source_url": "https://developers.binance.com/docs/binance-spot-api-docs/rest-api",
        "fetch_url": "https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md",
        "file": "binance-spot-rest-api.md",
        "owner": "Binance",
        "kind": "official-github-document",
        "notes": "Official binance-spot-api-docs repository; captured as Markdown because the developer page returned an empty 202 response in this environment.",
    },
    {
        "id": "binance-spot-websocket-streams",
        "title": "Binance Spot WebSocket Streams",
        "source_url": "https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams",
        "fetch_url": "https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/web-socket-streams.md",
        "file": "binance-spot-websocket-streams.md",
        "owner": "Binance",
        "kind": "official-github-document",
        "notes": "Official binance-spot-api-docs repository.",
    },
    {
        "id": "binance-alpha-sdk-readme",
        "title": "Binance official Alpha SDK README",
        "source_url": "https://developers.binance.com/docs/alpha/Introduction",
        "fetch_url": "https://raw.githubusercontent.com/binance/binance-connector-python/master/clients/alpha/README.md",
        "file": "binance-alpha-sdk-readme.md",
        "owner": "Binance",
        "kind": "official-sdk",
        "notes": "Official Alpha API SDK; Alpha is a separate discovery/market-data namespace and is not the same as Spot listing or Futures OI.",
    },
    {
        "id": "binance-alpha-market-data",
        "title": "Binance Alpha market-data endpoint definitions",
        "source_url": "https://developers.binance.com/docs/alpha/market-data",
        "fetch_url": "https://raw.githubusercontent.com/binance/binance-connector-python/master/clients/alpha/src/binance_sdk_alpha/rest_api/api/market_data_api.py",
        "file": "binance-alpha-market_data_api.py",
        "owner": "Binance",
        "kind": "official-sdk-source",
        "notes": "Generated official Alpha endpoint definitions: token list, exchange info, klines, ticker, depth and aggregated trades.",
    },
    {
        "id": "binance-alpha-websocket",
        "title": "Binance Alpha WebSocket stream definitions",
        "source_url": "https://developers.binance.com/docs/alpha/websocket",
        "fetch_url": "https://raw.githubusercontent.com/binance/binance-connector-python/master/clients/alpha/src/binance_sdk_alpha/websocket_streams/websocket_streams.py",
        "file": "binance-alpha-websocket_streams.py",
        "owner": "Binance",
        "kind": "official-sdk-source",
        "notes": "Generated official Alpha WebSocket stream definitions.",
    },
    {
        "id": "binance-sdk-spot-readme",
        "title": "Binance official modular Spot SDK README",
        "source_url": "https://github.com/binance/binance-connector-python/tree/master/clients/spot",
        "fetch_url": "https://raw.githubusercontent.com/binance/binance-connector-python/master/clients/spot/README.md",
        "file": "binance-sdk-spot-readme.md",
        "owner": "Binance",
        "kind": "official-sdk",
        "notes": "Official MIT-licensed modular connector; reference only, not a runtime dependency decision.",
    },
    {
        "id": "binance-sdk-usds-futures-readme",
        "title": "Binance official USDⓈ-M Futures SDK README",
        "source_url": "https://github.com/binance/binance-connector-python/tree/master/clients/derivatives_trading_usds_futures",
        "fetch_url": "https://raw.githubusercontent.com/binance/binance-connector-python/master/clients/derivatives_trading_usds_futures/README.md",
        "file": "binance-sdk-usds-futures-readme.md",
        "owner": "Binance",
        "kind": "official-sdk",
        "notes": "Official modular connector; endpoint links point back to developers.binance.com.",
    },
    {
        "id": "binance-sdk-usds-futures-market",
        "title": "Binance USDⓈ-M Futures market endpoint definitions",
        "source_url": "https://github.com/binance/binance-connector-python/tree/master/clients/derivatives_trading_usds_futures",
        "fetch_url": "https://raw.githubusercontent.com/binance/binance-connector-python/master/clients/derivatives_trading_usds_futures/src/binance_sdk_derivatives_trading_usds_futures/rest_api/rest_api.py",
        "file": "binance-sdk-usds-futures-market_api.py",
        "owner": "Binance",
        "kind": "official-sdk-source",
        "notes": "Generated official SDK endpoint definitions; filename may change upstream, so manifest must be refreshed when updating.",
    },
    {
        "id": "okx-api-v5",
        "title": "OKX API V5",
        "source_url": "https://www.okx.com/docs-v5/en/",
        "fetch_url": "https://www.okx.com/docs-v5/en/",
        "file": "okx-api-v5.html",
        "owner": "OKX",
        "kind": "official-web-document",
        "notes": "Dynamic documentation page; local copy is a retrieval snapshot and may not include content loaded by later JavaScript requests.",
    },
    {
        "id": "gate-api-futures",
        "title": "Gate API v4 Futures API reference",
        "source_url": "https://www.gate.com/docs/developers/apiv4/en/",
        "fetch_url": "https://raw.githubusercontent.com/gateio/gateapi-python/master/docs/FuturesApi.md",
        "file": "gate-api-futures.md",
        "owner": "Gate.io",
        "kind": "official-sdk-document",
        "notes": "Gate docs page returned 403 in this environment; captured official gateio/gateapi-python generated reference instead.",
    },
    {
        "id": "gate-api-python-readme",
        "title": "Gate API Python SDK README",
        "source_url": "https://github.com/gateio/gateapi-python",
        "fetch_url": "https://raw.githubusercontent.com/gateio/gateapi-python/master/README.md",
        "file": "gate-api-python-readme.md",
        "owner": "Gate.io",
        "kind": "official-sdk",
        "notes": "Official SDK README for authentication and package usage context.",
    },
    {
        "id": "bitget-uta-introduction",
        "title": "Bitget Unified Trading Account API",
        "source_url": "https://www.bitget.com/api-doc/uta/",
        "fetch_url": "https://www.bitget.com/docs/uta/Introduction",
        "file": "bitget-uta-introduction.html",
        "owner": "Bitget",
        "kind": "official-web-document",
        "notes": "Current UTA documentation route; captured after redirect.",
    },
    {
        "id": "hyperliquid-info-api",
        "title": "Hyperliquid Info API",
        "source_url": "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint",
        "fetch_url": "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint",
        "file": "hyperliquid-info-api.html",
        "owner": "Hyperliquid",
        "kind": "official-web-document",
        "notes": "GitBook page snapshot; API responses remain the runtime source of truth.",
    },
    {
        "id": "hyperliquid-websocket",
        "title": "Hyperliquid WebSocket API",
        "source_url": "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket",
        "fetch_url": "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket",
        "file": "hyperliquid-websocket.html",
        "owner": "Hyperliquid",
        "kind": "official-web-document",
        "notes": "GitBook page snapshot.",
    },
    {
        "id": "aster-api-documentation",
        "title": "Aster API documentation",
        "source_url": "https://docs.asterdex.com/for-developers/aster-api/api-documentation",
        "fetch_url": "https://docs.asterdex.com/for-developers/aster-api/api-documentation",
        "file": "aster-api-documentation.html",
        "owner": "Aster",
        "kind": "official-web-document",
        "notes": "Dynamic documentation page; use V3 for new integrations.",
    },
    {
        "id": "aster-futures-v3",
        "title": "Aster Futures API V3",
        "source_url": "https://github.com/asterdex/api-docs",
        "fetch_url": "https://raw.githubusercontent.com/asterdex/api-docs/master/V3%28Recommended%29/EN/aster-finance-futures-api-v3.md",
        "file": "aster-futures-v3.md",
        "owner": "Aster",
        "kind": "official-github-document",
        "notes": "Official asterdex/api-docs repository; V3 Recommended English reference.",
    },
    {
        "id": "aster-connector-python",
        "title": "Aster official Python connector README",
        "source_url": "https://github.com/asterdex/aster-connector-python",
        "fetch_url": "https://raw.githubusercontent.com/asterdex/aster-connector-python/master/README.md",
        "file": "aster-connector-python-readme.md",
        "owner": "Aster",
        "kind": "official-sdk",
        "notes": "Reference for API client patterns; first release uses public market data only.",
    },
    {
        "id": "cmc-api",
        "title": "CoinMarketCap API documentation",
        "source_url": "https://coinmarketcap.com/api/documentation/",
        "fetch_url": "https://coinmarketcap.com/api/documentation/",
        "file": "coinmarketcap-api.html",
        "owner": "CoinMarketCap",
        "kind": "official-web-document",
        "notes": "Dynamic API reference; key, plan, attribution and redistribution limits must be checked before production use.",
    },
    {
        "id": "coingecko-api",
        "title": "CoinGecko API documentation",
        "source_url": "https://docs.coingecko.com/",
        "fetch_url": "https://docs.coingecko.com/",
        "file": "coingecko-api.html",
        "owner": "CoinGecko",
        "kind": "official-web-document",
        "notes": "Dynamic documentation page; plan-specific limits are not inferred from page availability.",
    },
    {
        "id": "dexscreener-api",
        "title": "DexScreener API reference",
        "source_url": "https://docs.dexscreener.com/api/reference",
        "fetch_url": "https://docs.dexscreener.com/api/reference",
        "file": "dexscreener-api.html",
        "owner": "DexScreener",
        "kind": "official-web-document",
        "notes": "Reference for pair, price, liquidity and token-profile discovery; endpoint quotas still apply.",
    },
    {
        "id": "surf-data-api",
        "title": "Surf Data API overview",
        "source_url": "https://docs.asksurf.ai/data-api/overview",
        "fetch_url": "https://docs.asksurf.ai/data-api/overview",
        "file": "surf-data-api.html",
        "owner": "Surf",
        "kind": "official-web-document",
        "notes": "Supplemental data source; key/credits and coverage are separate from the free core feed.",
    },
    {
        "id": "surf-hyperliquid-positions",
        "title": "Surf Hyperliquid positions endpoint",
        "source_url": "https://docs.asksurf.ai/data-api/hyperliquid/positions",
        "fetch_url": "https://docs.asksurf.ai/data-api/hyperliquid/positions",
        "file": "surf-hyperliquid-positions.html",
        "owner": "Surf",
        "kind": "official-web-document",
        "notes": "Address-scoped positions; does not prove complete market-wide position coverage.",
    },
    {
        "id": "lightweight-charts",
        "title": "TradingView Lightweight Charts documentation",
        "source_url": "https://tradingview.github.io/lightweight-charts/docs",
        "fetch_url": "https://tradingview.github.io/lightweight-charts/docs",
        "file": "tradingview-lightweight-charts-docs.html",
        "owner": "TradingView",
        "kind": "official-open-source-document",
        "notes": "Apache 2.0 charting library documentation; this is separate from Advanced Charts/Charting Library licensing.",
    },
    {
        "id": "talib-functions",
        "title": "TA-Lib function reference",
        "source_url": "https://ta-lib.org/functions/",
        "fetch_url": "https://ta-lib.org/functions/",
        "file": "ta-lib-functions.html",
        "owner": "TA-Lib",
        "kind": "official-web-document",
        "notes": "Standard indicator and candlestick function reference; complex shapes such as arc bottoms require project rules.",
    },
]

ctx = ssl._create_unverified_context()
manifest = {
    "schema_version": 1,
    "project": "erji",
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "tls_note": "This capture script used Python's unverified SSL context because the local Windows runtime failed certificate negotiation for several public hosts. Verify certificates in the production fetcher.",
    "documents": [],
}

for item in DOCS:
    started = time.time()
    record = dict(item)
    record["fetched_at_utc"] = datetime.now(timezone.utc).isoformat()
    record["status"] = "error"
    record["http_status"] = None
    record["final_url"] = None
    record["content_type"] = None
    record["bytes"] = 0
    record["sha256"] = None
    record["saved_path"] = str((OUT / item["file"]).relative_to(ROOT)).replace("\\", "/")
    record["elapsed_seconds"] = None
    try:
        req = Request(item["fetch_url"], headers={
            "User-Agent": "erji-official-doc-fetch/0.4 (+local research tool)",
            "Accept": "text/html, text/plain, application/json, */*",
        })
        with urlopen(req, context=ctx, timeout=60) as response:
            body = response.read(16 * 1024 * 1024 + 1)
            if len(body) > 16 * 1024 * 1024:
                raise ValueError("response exceeds 16 MiB safety limit")
            record["http_status"] = getattr(response, "status", None)
            record["final_url"] = response.geturl()
            record["content_type"] = response.headers.get("Content-Type")
        target = OUT / item["file"]
        target.write_bytes(body)
        record["bytes"] = len(body)
        record["sha256"] = hashlib.sha256(body).hexdigest()
        record["status"] = "ok" if record["http_status"] == 200 and body else "empty"
    except HTTPError as exc:
        record["http_status"] = exc.code
        record["final_url"] = getattr(exc, "url", None)
        record["error"] = f"HTTPError: {exc.reason}"
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["elapsed_seconds"] = round(time.time() - started, 3)
    manifest["documents"].append(record)
    print(f"{record['status']:7} {record['http_status']!s:4} {record['bytes']:8} {item['id']}", flush=True)

MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
checksums = []
for record in manifest["documents"]:
    if record.get("sha256"):
        checksums.append(f"{record['sha256']}  {record['saved_path']}")
(ROOT / "docs" / "api" / "official" / "SHA256SUMS.txt").write_text("\n".join(checksums) + ("\n" if checksums else ""), encoding="utf-8")

ok = sum(1 for d in manifest["documents"] if d["status"] == "ok")
print(f"saved={ok}/{len(manifest['documents'])} manifest={MANIFEST_PATH}")
if ok != len(manifest["documents"]):
    print("Some documents were not captured; see manifest.json for errors.", file=sys.stderr)

