#!/usr/bin/env python3
"""ธาตุ & จาน backend: serves the site and the /api/* routes, and researches favorite shops' menus.

Run:  python3 server.py [--port 8000]
No third-party packages. Data is stored in data/shops.json next to this file.

Research, per shop (manual menus are never overwritten):
  1. Start URL = the shop's menu link, or a built-in catalog for known chains (7-Eleven).
  2. Read schema.org JSON-LD on the page (Menu / MenuItem / Product / ItemList).
  3. If the page is a catalog, follow same-site links under the same path (up to MAX_PAGES)
     and read each product page (JSON-LD, else og:title / <h1>, price and description).
Shops without a link and without a known catalog are marked as needing a link: no search
engine is used, and nothing is invented. Every shop is re-checked every REFRESH_HOURS.
"""
import argparse, html, ipaddress, json, os, re, socket, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data', 'shops.json')
REFRESH_HOURS = 6
MAX_PAGES = 40
UA = 'ElementTable/1.0 (menu research for personal use)'
KNOWN_CATALOGS = [  # (name pattern, catalog start URLs)
    (re.compile(r'7\s*-?\s*(11|eleven)|เซเว่น|เซเว่นอีเลฟเว่น', re.I),
     ['https://www.7eleven.co.th/new-product']),
]
INGREDIENT_WORDS = ['ข้าว', 'ไข่', 'ไก่', 'หมู', 'เนื้อ', 'กุ้ง', 'ปลา', 'ปู', 'หมึก', 'แฮม', 'เบคอน', 'ไส้กรอก',
    'ชีส', 'เนย', 'นม', 'นมสด', 'กะทิ', 'มะพร้าว', 'ผักโขม', 'คะน้า', 'บรอกโคลี', 'มะเขือเทศ', 'ฟักทอง', 'ข้าวโพด',
    'เห็ด', 'เต้าหู้', 'ถั่วเหลือง', 'งา', 'งาดำ', 'ถั่วดำ', 'สาหร่าย', 'กล้วย', 'ช็อกโกแลต', 'ขนมปัง', 'แป้ง',
    'พริก', 'กระเทียม', 'ขิง', 'โหระพา', 'กะเพรา', 'กระเพรา', 'อะโวคาโด', 'มันเทศ', 'ไรซ์เบอร์รี่', 'ข้าวหอมมะลิ',
    'ข้าวญี่ปุ่น', 'แครอท', 'ถั่วฝักยาว', 'ข้าวโพดอ่อน', 'มอสซาเรลล่า', 'เชดด้า', 'พาเมซาน', 'มายองเนส', 'โคชูจัง',
    'ต๊อกบกกี', 'กุนเชียง', 'เห็ดหอม', 'ไข่แดงเค็ม', 'ปูอัด', 'ลูกชิ้นปลา', 'ปลาสลิด', 'ยอดมะพร้าว', 'ขึ้นฉ่าย',
    'ข่า', 'ตะไคร้', 'มะเขือ', 'นูเทลล่า', 'เฮเซลนัท', 'กาแฟ', 'มัทฉะ', 'ชาเขียว', 'ชา', 'น้ำผึ้ง', 'มะนาว', 'โซดา',
    'คาราเมล', 'เปปเปอร์มินต์', 'ถั่วแระ', 'พริกไทยดำ', 'พริกไทย', 'ซอสมะเขือเทศ']
lock = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


def load():
    try:
        with open(DATA, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save(shops):
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    tmp = DATA + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(shops, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA)


# ---------- fetching ----------
def public_host(url):
    """Refuse non-http(s) URLs and hosts that resolve to private/loopback addresses."""
    u = urlparse(url)
    if u.scheme not in ('http', 'https') or not u.hostname:
        return False
    if os.environ.get('ELEMENT_ALLOW_PRIVATE') == '1':  # used by the test fixture only
        return True
    try:
        for info in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except socket.gaierror:
        # Behind an HTTP(S) proxy the name may only resolve at the proxy; let the proxy decide.
        return bool(os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy'))
    return True


def fetch(url, limit=2_000_000):
    if not public_host(url):
        raise ValueError('ลิงก์นี้ใช้ไม่ได้ (ต้องเป็นเว็บไซต์สาธารณะ http/https)')
    req = Request(url, headers={'User-Agent': UA, 'Accept-Language': 'th,en;q=0.8'})
    with urlopen(req, timeout=20) as r:
        raw = r.read(limit)
        charset = r.headers.get_content_charset() or 'utf-8'
        return r.geturl(), raw.decode(charset, 'replace')


# ---------- parsing ----------
def text(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()


def meta(page, prop):
    m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)' % re.escape(prop), page, re.I) \
        or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(prop), page, re.I)
    return text(m.group(1)) if m else ''


def price_of(s):
    m = re.search(r'(?:฿\s*|ราคา\s*)(\d{1,5}(?:\.\d{1,2})?)|(\d{1,5}(?:\.\d{1,2})?)\s*(?:บาท|฿|THB)', s or '')
    return float(m.group(1) or m.group(2)) if m else None


# Thai has no spaces between words, so a few short words need context to avoid false hits
# (คาปูชิโน่ is not crab, ขนม is not milk, กับข้าว is not rice, เนื้อนุ่ม is texture, พลังงาน is not sesame).
WORD_RULES = {
    'ปู': r'(?<!คา)ปู(?!ชิ)', 'นม': r'(?<!ข)นม', 'ข้าว': r'(?<!กับ)ข้าว(?!โพด)', 'งา': r'งา(?![นม])',
    'ชา': r'ชา(?=เขียว|นม|มะนาว|ไทย|ดำ|\b)|^ชา', 'ไข่': r'(?<!กล้วย)ไข่', 'เนื้อ': r'เนื้อ(?=วัว|สัตว์)',
    'ปลา': r'(?<!น้ำ)ปลา', 'แป้ง': r'แป้ง(?!เอแคลร์)', 'ข่า': r'(?<![ก-๙])ข่า|ต้มข่า',
    'มะเขือ': r'มะเขือ(?!เทศ)', 'พริกไทย': r'พริกไทย(?!ดำ)', 'ข้าวโพด': r'ข้าวโพด(?!อ่อน)',
}
MEATS = {'ไก่', 'หมู', 'เนื้อ', 'กุ้ง', 'ปลา', 'ปู', 'หมึก', 'แฮม', 'เบคอน', 'ไส้กรอก', 'กุนเชียง', 'ไข่', 'ไข่แดงเค็ม',
         'ปูอัด', 'ลูกชิ้นปลา', 'ปลาสลิด'}


def ingredients_of(s):
    s = s or ''
    found = [w for w in INGREDIENT_WORDS if re.search(WORD_RULES.get(w, re.escape(w)), s)]
    # drop a word when a longer found word already contains it (นมสด covers นม)
    return [w for w in found if not any(w != f and w in f for f in found)]


def role_of(name):
    if re.search(r'น้ำ(?!พริก|ซุป|ปลา)|ชา|กาแฟ|นม(?!ปัง)|โกโก้|เครื่องดื่ม|โซดา|drink|tea|coffee|juice', name, re.I):
        return 'drink'
    if re.search(r'ขนม(?!จีบ)|เค้ก|คุกกี้|ไอศกรีม|ของว่าง|เอแคลร์|ปังปิ้ง|กล้วย|ถั่วแระ|snack|dessert|ผลไม้', name, re.I):
        return 'side'
    return 'main'


def item(name, price=None, desc='', url=''):
    name = text(name)[:120]
    ings = ingredients_of(name + ' ' + (desc or ''))
    return {'name': name, 'price': price, 'currency': 'THB', 'ingredients': ings, 'ingredientsKnown': False,
            'element': None, 'role': role_of(name), 'vegetarian': None, 'origin': 'source',
            'sourceUrl': url, 'observedAt': now(), 'description': text(desc)[:300]}




def nextdata_items(page, url):
    """7-Eleven Thailand (Next.js): product lists live in __NEXT_DATA__ pageProps.<category>.items."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return []
    try:
        props = json.loads(m.group(1))['props']['pageProps']
    except (ValueError, KeyError, TypeError):
        return []
    base = f'{urlparse(url).scheme}://{urlparse(url).netloc}'
    links = {h.split('/')[3].split('-')[0]: h for h in re.findall(r'href="(/new-product/[^/"]+/[^"]+)"', page)}
    raw = []
    for cat, block in props.items():
        if not isinstance(block, dict) or not isinstance(block.get('items'), list) or cat in ('heroBanner', 'recommended'):
            continue
        for it in block['items']:
            if not isinstance(it, dict) or not it.get('title_th') or it.get('is_active') == 0:
                continue
            detail = text(it.get('detail_th') or '')
            prices = [float(x) for x in re.findall(r'ราคา\s*:?\s*(\d{1,4})\s*บาท', detail)]
            try:
                price = float(it['price']) if it.get('price') else (min(prices) if prices else None)
            except (TypeError, ValueError):
                price = min(prices) if prices else None
            size = re.search(r'(\d{1,2})\s*oz', detail)
            raw.append((cat, it, detail, price, size and f'{size.group(1)} oz', len(set(prices)) > 1))
    names = [text(x[1]['title_th']) for x in raw]
    out = []
    for (cat, it, detail, price, size, multi), name in zip(raw, names):
        if names.count(name) > 1 and size:
            name = f'{name} {size}'
        desc = text(it.get('desc_th') or '') + ' ' + detail
        m_ = item(name, price, desc[:600], base + links.get(str(it['id']), f'/new-product/{cat}'))
        m_['role'] = 'drink' if cat == 'drink' else ('side' if role_of(name) == 'side' else 'main')
        m_['vegetarian'] = False if MEATS & set(ingredients_of(name + ' ' + desc)) or 'ซีฟู้ด' in name else None
        if multi:
            m_['priceNote'] = 'มีหลายขนาด ราคาเริ่มต้น'
        m_['category'] = cat
        out.append(m_)
    return out


def jsonld_items(page, url):
    out = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(html.unescape(block.strip()))
        except ValueError:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
                continue
            if not isinstance(d, dict):
                continue
            t = d.get('@type')
            t = t if isinstance(t, list) else [t]
            if ('MenuItem' in t or 'Product' in t) and d.get('name'):
                offers = d.get('offers') or {}
                offers = offers[0] if isinstance(offers, list) and offers else offers
                p = offers.get('price') if isinstance(offers, dict) else None
                try:
                    p = float(p) if p not in (None, '') else None
                except (TypeError, ValueError):
                    p = None
                out.append(item(d['name'], p, d.get('description', ''), d.get('url') and urljoin(url, d['url']) or url))
            for k in ('@graph', 'hasMenuSection', 'hasMenuItem', 'itemListElement', 'item', 'mainEntity'):
                if k in d:
                    stack.append(d[k])
    return out


def product_page(page, url):
    found = jsonld_items(page, url)
    if found:
        return found[:1]
    name = meta(page, 'og:title') or text((re.search(r'<h1[^>]*>(.*?)</h1>', page, re.S | re.I) or [None, ''])[1])
    if not name:
        return []
    desc = meta(page, 'og:description') or meta(page, 'description')
    body = text(page[:200000])
    return [item(re.sub(r'\s*[|\-–]\s*(7-?Eleven|เซเว่น).*$', '', name, flags=re.I), price_of(desc) or price_of(body[:4000]), desc, url)]


def catalog_links(page, url):
    base = urlparse(url)
    prefix = base.path if base.path.endswith('/') else base.path.rsplit('/', 1)[0] + '/'
    links = []
    for href in re.findall(r'href=["\']([^"\'#]+)', page, re.I):
        u = urlparse(urljoin(url, html.unescape(href)))
        if u.netloc == base.netloc and u.path.startswith(prefix) and u.path.rstrip('/') != base.path.rstrip('/'):
            full = f'{u.scheme}://{u.netloc}{u.path}'
            if full not in links and not re.search(r'\.(jpe?g|png|gif|webp|svg|css|js|pdf)$', u.path, re.I):
                links.append(full)
    return links


def research(start_urls):
    """Returns (menus, pages_read). Raises when nothing could be fetched at all."""
    menus, seen, pages, errors = {}, set(), 0, []

    def add(items):
        for m in items:
            if m['name'] and m['name'] not in menus:
                menus[m['name']] = m

    queue = []
    for start in start_urls:
        try:
            real, page = fetch(start)
        except Exception as e:  # noqa: BLE001 - report any fetch failure to the user
            errors.append(f'{start}: {e}')
            continue
        pages += 1
        seen.add(real)
        catalog = nextdata_items(page, real)
        if catalog:
            add(catalog)
            continue
        direct = jsonld_items(page, real)
        add(direct)
        links = catalog_links(page, real)
        if not direct and not links:
            add(product_page(page, real))
        queue += [u for u in links if u not in seen]
    # one more level for category → product catalogs
    def read(u):
        try:
            real, page = fetch(u)
            return real, page
        except Exception:  # noqa: BLE001
            return None
    queue = list(dict.fromkeys(queue))[:MAX_PAGES]
    with ThreadPoolExecutor(6) as pool:
        results = [r for r in pool.map(read, queue) if r]
    second = []
    for real, page in results:
        pages += 1
        sub = catalog_links(page, real)
        if sub and not jsonld_items(page, real):
            second += [u for u in sub if u not in seen and u not in queue]
        else:
            add(product_page(page, real))
    if second and len(menus) < MAX_PAGES:
        second = list(dict.fromkeys(second))[:MAX_PAGES - len(menus)]
        with ThreadPoolExecutor(6) as pool:
            for r in pool.map(read, second):
                if r:
                    pages += 1
                    add(product_page(r[1], r[0]))
    if not pages:
        raise RuntimeError('; '.join(errors) or 'เปิดลิงก์ไม่ได้')
    return list(menus.values()), pages


def start_urls(shop):
    if shop.get('sourceUrl'):
        return [shop['sourceUrl']]
    for pat, urls in KNOWN_CATALOGS:
        if pat.search(shop.get('name', '')):
            return urls
    return []


def refresh(shop):
    shop['lastChecked'] = now()
    urls = start_urls(shop)
    if not urls:
        shop.update(status='research_pending', researchState='needs_info',
                    researchNote='ยังไม่มีลิงก์เมนู ใส่ลิงก์หน้าเมนูของร้าน (เว็บร้าน หรือหน้าเมนูที่เปิดดูได้สาธารณะ) เพื่อให้ระบบดึงเมนูให้',
                    error=None)
        return shop
    try:
        menus, pages = research(urls)
    except Exception as e:  # noqa: BLE001
        shop.update(status='error', error=f'ดึงข้อมูลไม่สำเร็จ ใช้ข้อมูลเดิมไว้ก่อน ({str(e)[:160]})')
        return shop
    if not menus:
        shop.update(status='research_pending', researchState='done', error=None,
                    researchNote=f'อ่าน {pages} หน้าแล้ว แต่ไม่พบรายการเมนูที่อ่านได้ (เช่น เมนูเป็นรูปภาพ) เพิ่มเมนูเองได้ที่ "แก้ไข"')
        return shop
    old = {m['name'] for m in shop.get('sourceMenus', [])}
    shop.update(sourceMenus=menus, status='ok', researchState='done', lastSuccess=now(), error=None,
                changes={'added': len([m for m in menus if m['name'] not in old])},
                researchNote=f'อ่าน {pages} หน้า พบ {len(menus)} รายการ • วัตถุดิบดึงจากคำในชื่อ/คำอธิบาย ยังไม่ใช่ฉลากครบถ้วน • ราคาและสต็อกสาขาต้องตรวจอีกครั้ง')
    return shop


def refresh_ids(ids=None):
    with lock:
        shops = load()
    targets = [s for s in shops if not s.get('archived') and s.get('auto', True) and (ids is None or s['id'] in ids)]
    with ThreadPoolExecutor(4) as pool:
        done = {s['id']: s for s in pool.map(refresh, [dict(s) for s in targets])}
    with lock:
        shops = load()  # merge onto the latest copy so edits made meanwhile survive
        for s in shops:
            if s['id'] in done:
                fresh = done[s['id']]
                for k in ('sourceMenus', 'status', 'researchState', 'lastSuccess', 'lastChecked', 'error', 'changes', 'researchNote'):
                    if k in fresh:
                        s[k] = fresh[k]
        save(shops)
    return [s for s in shops if s['id'] in done]


def scheduler():
    while True:
        time.sleep(60)
        cutoff = time.time() - REFRESH_HOURS * 3600
        with lock:
            due = [s['id'] for s in load() if not s.get('archived') and s.get('auto', True)
                   and (not s.get('lastChecked') or datetime.fromisoformat(s['lastChecked']).timestamp() < cutoff)]
        if due:
            refresh_ids(set(due))


# ---------- HTTP ----------
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def send_json(self, body, status=200):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith('/data/'):
            return self.send_error(404)
        if self.path == '/api/health':
            return self.send_json({'ok': True, 'mode': 'server'})
        if self.path == '/api/restaurants':
            with lock:
                return self.send_json({'restaurants': [s for s in load() if not s.get('archived')], 'mode': 'server'})
        return super().do_GET()

    def do_POST(self):
        try:
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        except ValueError:
            return self.send_json({'error': 'ข้อมูลไม่ถูกต้อง'}, 400)
        path = self.path[len('/api/'):] if self.path.startswith('/api/') else ''
        if path == 'restaurants':
            name = str(data.get('name') or '').strip()[:120]
            if not name:
                return self.send_json({'error': 'กรุณาใส่ชื่อร้าน'}, 400)
            src = str(data.get('sourceUrl') or '').strip()
            if src and urlparse(src).scheme not in ('http', 'https'):
                return self.send_json({'error': 'ลิงก์ต้องขึ้นต้นด้วย http:// หรือ https://'}, 400)
            with lock:
                shops = load()
                s = next((x for x in shops if x['id'] == data.get('id')), None)
                if not s:
                    s = {'id': str(uuid.uuid4()), 'sourceMenus': [], 'lastSuccess': None, 'lastChecked': None,
                         'status': 'queued', 'changes': None}
                    shops.append(s)
                s.update(name=name, area=str(data.get('area') or '')[:250], sourceUrl=src, auto=bool(data.get('auto')),
                         manualMenus=[{**m, 'origin': 'manual', 'ingredientsKnown': bool(m.get('ingredients'))}
                                      for m in (data.get('manualMenus') or [])], updatedAt=now(), archived=False)
                save(shops)
            if s['auto']:
                threading.Thread(target=refresh_ids, args=({s['id']},), daemon=True).start()
            return self.send_json({'restaurant': s})
        if path == 'archive':
            with lock:
                shops = load()
                for s in shops:
                    if s['id'] == data.get('id'):
                        s['archived'] = True
                save(shops)
            return self.send_json({'ok': True})
        if path == 'refresh':
            ids = {data['id']} if data.get('id') else None
            done = refresh_ids(ids)
            return self.send_json({'restaurant': done[0]} if ids and done else {'results': done, 'mode': 'server'})
        return self.send_json({'error': 'not found'}, 404)


def snapshot_7eleven(out):
    """Write the 7-Eleven catalog to a JS file the static page (no server) can load."""
    menus, pages = research(KNOWN_CATALOGS[0][1])
    body = {'fetchedAt': now(), 'source': KNOWN_CATALOGS[0][1][0], 'menus': menus}
    with open(out, 'w', encoding='utf-8') as f:
        f.write('/* Generated by: python3 server.py --snapshot-7eleven seven-eleven-catalog.js */\n')
        f.write('window.SEVEN_ELEVEN_CATALOG=' + json.dumps(body, ensure_ascii=False, indent=0) + ';\n')
    print(f'{len(menus)} menus from {pages} page(s) -> {out}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--snapshot-7eleven', metavar='OUT', help='write the 7-Eleven catalog for the static page and exit')
    a = ap.parse_args()
    if a.snapshot_7eleven:
        return snapshot_7eleven(a.snapshot_7eleven)
    threading.Thread(target=scheduler, daemon=True).start()
    print(f'ธาตุ & จาน: http://{a.host}:{a.port}  (data: {DATA})')
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
