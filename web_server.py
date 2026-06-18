#!/usr/bin/env python3
import os, uvicorn, subprocess, json, xml.etree.ElementTree as ET, urllib.parse, time, re, threading
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="加密市场情报分析")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

CONTRACTS_CACHE = []
CONTRACTS_TS = 0
NEWS_CACHE = {'ts': 0, 'items': []}
CONTRACTS_LOCK = threading.Lock()
STOCK_CONTRACTS_CACHE = []
STOCK_CONTRACTS_TS = 0
BINANCE_FAPI_HOSTS = [
    'https://fapi.binance.com',
]
STOCK_PERP_BASES = {
    'TSLA', 'AAPL', 'SPY', 'QQQ', 'TSM', 'MSTR', 'AMZN', 'COIN', 'PLTR',
    'CRCL', 'LLY', 'NVO', 'BB', 'NOK', 'EWT', 'ASTS', 'NVDA', 'MSFT',
    'META', 'GOOGL', 'AMD', 'NFLX'
}
STOCK_PERP_SYMBOLS = {f'{base}USDT' for base in STOCK_PERP_BASES}

def curl_get(url, timeout=20):
    try:
        r = subprocess.run(['curl', '-s', '-L', '--max-time', str(timeout), '--tls-max', '1.2', url],
                         capture_output=True, text=True, timeout=timeout+5)
        if r.returncode == 0 and r.stdout:
            return json.loads(r.stdout)
    except: pass
    return None

def binance_get(path, timeout=15):
    for host in BINANCE_FAPI_HOSTS:
        d = curl_get(host + path, timeout)
        if d and not (isinstance(d, dict) and d.get('code') == 0 and 'restricted location' in d.get('msg', '').lower()):
            return d
    return None

def load_binance_stock_contracts():
    global STOCK_CONTRACTS_CACHE, STOCK_CONTRACTS_TS
    now = time.time()
    if now - STOCK_CONTRACTS_TS < 30:
        return list(STOCK_CONTRACTS_CACHE)
    d = binance_get('/fapi/v1/ticker/24hr', 6)
    if not isinstance(d, list):
        STOCK_CONTRACTS_TS = now
        return list(STOCK_CONTRACTS_CACHE)
    contracts = []
    for t in d:
        raw_sym = t.get('symbol', '')
        if raw_sym not in STOCK_PERP_SYMBOLS:
            continue
        price = float(t.get('lastPrice', 0) or 0)
        vol = float(t.get('quoteVolume', 0) or 0)
        if price <= 0 or vol <= 0:
            continue
        base = raw_sym[:-4]
        contracts.append({
            'symbol': f'{base}-USDT',
            'price': price,
            'change_24h': round(float(t.get('priceChangePercent', 0) or 0), 2),
            'turnover_24h': vol,
            'source': 'binance',
            'market_type': 'stock_perp',
        })
    STOCK_CONTRACTS_CACHE = contracts
    STOCK_CONTRACTS_TS = now
    return contracts

def zh_polish(text):
    pairs = {
        'Binance': '币安', 'OpenAI': '人工智能公司奥特曼团队', 'Anthropic': '人工智能公司安斯罗匹克',
        'MiCA': '欧盟加密资产市场法规', 'EU': '欧盟', 'EEA': '欧洲经济区',
        'DeFi': '去中心化金融', 'Aave': '去中心化借贷协议', 'TVL': '总锁仓价值',
        'Chainlink': '预言机网络', 'Ripple': '瑞波', 'Flutterwave': '非洲支付公司弗拉特威夫',
        'Tether': '泰达币公司', 'DMCC': '迪拜商品中心', 'Dubai': '迪拜',
        'PancakeSwap': '薄饼交易所', 'Pudgy Penguins': '胖企鹅', 'MANTRA': '曼特拉公链',
        'Inveniam': '英维尼亚姆资产数据平台', 'Ready Card': '锐迪支付卡', 'KRWQ': '韩元稳定币',
        'stablecoin': '稳定币', 'blockchain': '区块链',
    }
    for src, dst in pairs.items():
        text = text.replace(src, dst)
    text = re.sub(r'(\d+(?:\.\d+)?)\s*B\b', r'\1十亿', text)
    text = re.sub(r'[A-Za-z][A-Za-z0-9.-]*', '相关项目', text)
    text = re.sub(r'\s+', ' ', text)
    return text.replace("'", '').replace('’', '').strip()

def translate_titles(titles):
    if not titles:
        return []
    out = []
    batch = []
    batch_len = 0
    for title in titles:
        text = (title or '').strip()
        if batch and batch_len + len(text) > 1200:
            out.extend(translate_titles(batch))
            batch, batch_len = [], 0
        batch.append(text)
        batch_len += len(text)
    if len(batch) < len(titles):
        out.extend(translate_titles(batch))
        return out if len(out) == len(titles) else []
    sep = '\n@@@\n'
    q = sep.join(batch)
    url = 'https://translate.googleapis.com/translate_a/single?' + urllib.parse.urlencode({
        'client': 'gtx', 'sl': 'en', 'tl': 'zh-CN', 'dt': 't', 'q': q
    })
    try:
        r = subprocess.run(['curl', '-s', '--max-time', '12', '--tls-max', '1.2', url],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not r.stdout:
            return []
        data = json.loads(r.stdout)
        text = ''.join(part[0] for part in data[0] if part and part[0])
        translated = [zh_polish(t.strip()) for t in text.split('@@@')]
        return translated if len(translated) == len(batch) else []
    except:
        return []

def clean_text(text):
    text = re.sub(r'<[^>]+>', ' ', text or '')
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&quot;', '"')
    text = text.replace('&#8217;', '’').replace('&#8211;', '-').replace('&#8230;', '...')
    return re.sub(r'\s+', ' ', text).strip()

def load_contracts(force=False):
    global CONTRACTS_CACHE, CONTRACTS_TS
    now = time.time()
    if CONTRACTS_CACHE and not force and now - CONTRACTS_TS < 1:
        return
    d = curl_get('https://api.kucoin.com/api/v1/market/allTickers', 25)
    if d and d.get('code') == '200000':
        contracts = []
        for t in d['data']['ticker']:
            sym = t.get('symbol', '')
            if not sym.endswith('-USDT'): continue
            price = float(t.get('last', 0) or 0)
            vol = float(t.get('volValue', 0) or 0)
            if price <= 0 or vol <= 0: continue
            contracts.append({'symbol': sym, 'price': price,
                'change_24h': round(float(t.get('changeRate', 0) or 0) * 100, 2),
                'turnover_24h': vol, 'source': 'kucoin', 'market_type': 'crypto'})
        contracts.extend(load_binance_stock_contracts())
        contracts.sort(key=lambda x: x['turnover_24h'], reverse=True)
        with CONTRACTS_LOCK:
            CONTRACTS_CACHE = contracts
            CONTRACTS_TS = now
        print(f"✅ {len(CONTRACTS_CACHE)} 合约")

def contracts_loop():
    while True:
        load_contracts(force=True)
        time.sleep(1)

@app.on_event("startup")
def startup():
    load_contracts(force=True)
    threading.Thread(target=contracts_loop, daemon=True).start()

@app.get("/api/contracts")
def list_contracts(search: str = "", page: int = 1, limit: int = 100):
    global CONTRACTS_CACHE
    if not CONTRACTS_CACHE:
        load_contracts(force=True)
    with CONTRACTS_LOCK:
        contracts = list(CONTRACTS_CACHE)
    if search:
        s = search.upper().replace('USDT', '').replace('-', '')
        contracts = [c for c in contracts if s in c['symbol'].upper()]
    start = (page - 1) * limit
    return {'total': len(contracts), 'page': page, 'limit': limit,
            'contracts': contracts[start:start+limit]}

@app.get("/api/kline/{symbol}")
def kline_proxy(symbol: str, type: str = "1min"):
    intervals = {
        "1min": 60, "3min": 180, "5min": 300, "15min": 900, "30min": 1800,
        "1hour": 3600, "2hour": 7200, "4hour": 14400, "8hour": 28800,
        "12hour": 43200, "1day": 86400, "1week": 604800
    }
    if type not in intervals:
        type = "1min"
    end_at = int(time.time())
    start_at = end_at - intervals[type] * 500
    raw_binance_symbol = symbol.replace('-', '').upper()
    if raw_binance_symbol in STOCK_PERP_SYMBOLS:
        klines = binance_get(f'/fapi/v1/klines?symbol={raw_binance_symbol}&interval={type.replace("min","m").replace("hour","h").replace("day","d")}&limit=500', 25)
        if isinstance(klines, list):
            data = []
            for k in klines:
                if len(k) < 6:
                    continue
                data.append([str(int(k[0]) // 1000), str(k[1]), str(k[4]), str(k[2]), str(k[3]), str(k[5]), '0'])
            if data:
                return {'code': '200000', 'data': data}
        raise HTTPException(500, "美股永续K线获取失败")
    d = curl_get(f'https://api.kucoin.com/api/v1/market/candles?type={type}&symbol={symbol}&startAt={start_at}&endAt={end_at}', 25)
    if d and d.get('code') == '200000': return d
    raise HTTPException(500, "K线获取失败")

@app.get("/api/news")
def news():
    global NEWS_CACHE
    now = time.time()
    if NEWS_CACHE['items'] and now - NEWS_CACHE['ts'] < 300:
        return {'news': NEWS_CACHE['items']}
    try:
        r = subprocess.run(['curl', '-s', '--max-time', '10', '--tls-max', '1.2',
                          'https://en.cryptonomist.ch/feed/'], capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not r.stdout: return {'news': []}
        root = ET.fromstring(r.stdout.encode('utf-8'))
        items = []
        raw_items = []
        for item in root.findall('.//item')[:20]:
            title = item.findtext('title', '')
            desc = clean_text(item.findtext('description', ''))
            link = ''
            for l in item.findall('.//link'):
                if l.text and 'http' in l.text: link = l.text; break
            pub = item.findtext('pubDate', '')
            if title:
                raw_items.append({'title': title.strip(), 'summary': desc[:500], 'link': link, 'time': pub[:25] if pub else ''})
        titles = translate_titles([i['title'] for i in raw_items])
        summaries = translate_titles([i['summary'] or i['title'] for i in raw_items])
        if len(titles) != len(raw_items):
            titles = [zh_polish(i['title']) for i in raw_items]
        if len(summaries) != len(raw_items):
            summaries = [''] * len(raw_items)
        for item, title, summary in zip(raw_items, titles, summaries):
            if title:
                items.append({'title': title, 'summary': summary or title, 'link': item['link'], 'time': item['time']})
        NEWS_CACHE = {'ts': now, 'items': items}
        return {'news': items}
    except:
        return {'news': []}

@app.get("/api/fear-greed")
def fear_greed():
    d = curl_get('https://api.alternative.me/fng/?limit=1', 10)
    if d and 'data' in d:
        return {'value': int(d['data'][0]['value']), 'label': d['data'][0]['value_classification']}
    return {'value': None, 'label': 'N/A'}

BASE_DIR = Path(__file__).resolve().parent
template_file = BASE_DIR / 'web_template_v3.html'
template_parts = sorted((BASE_DIR / 'template_parts').glob('web_template_v3.html.part*'))
if template_file.exists():
    HTML = template_file.read_text(encoding='utf-8')
elif template_parts:
    HTML = ''.join(p.read_text(encoding='utf-8') for p in template_parts)
else:
    raise RuntimeError('web_template_v3.html not found')

@app.get("/")
def root():
    return HTMLResponse(HTML)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8888'))
    print(f"\n  📡 加密市场情报分析\n  http://127.0.0.1:8888\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
