"""Showtime: moderated, location-based incident reporting. Python 3.12+, no dependencies."""
import base64, hashlib, hmac, io, json, os, secrets, sqlite3, struct, threading, time, zlib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('DATA_DIR', ROOT / 'data'))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'reports.sqlite3'
PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
SESSIONS = {}
LIMITS = {}
LOCK = threading.Lock()
PUBLIC = 'id,kind,building,floor,side,machine,occurred,description,status,created'

def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    with connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS reports (
        id TEXT PRIMARY KEY, kind TEXT, building TEXT, floor TEXT, side TEXT, machine TEXT,
        occurred TEXT, description TEXT, reporter_building TEXT, room TEXT, image BLOB,
        status TEXT DEFAULT '待核实', created TEXT DEFAULT CURRENT_TIMESTAMP)''')

def sanitize_png(raw):
    """Accept only bounded RGB/RGBA PNG; rebuild without EXIF/text/location chunks."""
    if len(raw) > 6_000_000 or raw[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('请通过照片编辑器上传 PNG 照片')
    pos, chunks, compressed, header, ended = 8, [], bytearray(), None, False
    while pos < len(raw):
        if pos + 12 > len(raw): raise ValueError('照片损坏')
        n = struct.unpack('>I', raw[pos:pos+4])[0]
        kind = raw[pos+4:pos+8]
        body = raw[pos+8:pos+8+n]
        end = pos + 12 + n
        if end > len(raw) or zlib.crc32(kind + body) & 0xffffffff != int.from_bytes(raw[pos+8+n:end], 'big'):
            raise ValueError('照片损坏')
        if kind == b'IHDR':
            if header is not None or pos != 8 or n != 13: raise ValueError('照片格式无效')
            header = struct.unpack('>IIBBBBB', body)
            w,h,depth,color,comp,filt,interlace = header
            if not (0 < w <= 1600 and 0 < h <= 1600 and depth == 8 and color in (2,6) and comp == filt == interlace == 0):
                raise ValueError('照片尺寸或格式不支持')
        elif header is None: raise ValueError('照片缺少头部')
        if kind == b'IDAT': compressed.extend(body)
        if kind in (b'IHDR', b'IDAT', b'IEND'): chunks.append(raw[pos:end])
        elif kind[0] & 32 == 0: raise ValueError('照片包含不支持的格式')
        pos = end
        if kind == b'IEND':
            if n != 0 or pos != len(raw): raise ValueError('照片格式无效')
            ended = True
            break
    if not header or not ended: raise ValueError('照片不完整')
    expected = (w * (4 if color == 6 else 3) + 1) * h
    dec = zlib.decompressobj()
    pixels = dec.decompress(bytes(compressed), expected + 1)
    if len(pixels) != expected or not dec.eof or dec.unused_data or dec.unconsumed_tail:
        raise ValueError('照片像素数据无效')
    return raw[:8] + b''.join(chunks)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass  # Avoid logging private request data.
    def admin(self):
        cookie = self.headers.get('Cookie','')
        token = next((p.strip()[8:] for p in cookie.split(';') if p.strip().startswith('session=')), '')
        with LOCK: return SESSIONS.get(token, 0) > time.time()
    def send(self, status, body, content='application/json; charset=utf-8', cookie=None):
        if content.startswith('application/json'): body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',content)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob: data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers()
        self.wfile.write(body)
    def throttle(self, category, maximum):
        # Use direct peer; configure reverse proxy rate limits for real client IPs.
        key = (self.client_address[0],category)
        with LOCK:
            now = time.time()
            if len(LIMITS) > 10000:
                for old in list(LIMITS):
                    if LIMITS[old][1] < now: del LIMITS[old]
            count, expires = LIMITS.get(key,(0,now+3600))
            if expires < now: count,expires = 0,now+3600
            LIMITS[key] = (count+1,expires)
        return count < maximum
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/reports':
            with connect() as c:
                rows = c.execute(f"SELECT {PUBLIC} FROM reports WHERE status IN ('已发布','处理中','已处理') ORDER BY created DESC LIMIT 200").fetchall()
            return self.send(200,[dict(r) for r in rows])
        if path == '/api/admin':
            if not self.admin(): return self.send(401,{'error':'请先登录'})
            with connect() as c: rows = c.execute(f'SELECT {PUBLIC},reporter_building,room FROM reports ORDER BY created DESC LIMIT 500').fetchall()
            return self.send(200,[dict(r) for r in rows])
        if path.startswith('/api/photos/'):
            with connect() as c: row = c.execute('SELECT image,status FROM reports WHERE id=?',(path.split('/')[-1],)).fetchone()
            if not row or (row['status'] not in ('已发布','处理中','已处理') and not self.admin()):
                return self.send(404,{'error':'照片不存在'})
            return self.send(200,row['image'],'image/png')
        files = {'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
        if path in files:
            mime = {'/':'text/html; charset=utf-8','/app.js':'text/javascript; charset=utf-8','/style.css':'text/css; charset=utf-8'}[path]
            return self.send(200,(ROOT/'static'/files[path]).read_bytes(),mime)
        self.send(404,{'error':'页面不存在'})
    def do_POST(self):
        # Require browser same-origin fetch and JSON; cross-site forms cannot invoke mutations.
        if self.headers.get('Sec-Fetch-Site') not in (None,'same-origin','none'):
            return self.send(403,{'error':'不允许跨站请求'})
        if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
            return self.send(415,{'error':'需要 JSON 请求'})
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 8_200_000: return self.send(413,{'error':'请求过大'})
            d = json.loads(self.rfile.read(length))
            if not isinstance(d,dict): raise ValueError('请求无效')
            path = urlparse(self.path).path
            if path == '/api/login':
                if not self.throttle('login',20): return self.send(429,{'error':'尝试过多，请一小时后重试'})
                supplied = str(d.get('password',''))
                if not PASSWORD or not hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),hashlib.sha256(PASSWORD.encode()).digest()):
                    return self.send(401,{'error':'密码错误或管理员尚未配置'})
                token = secrets.token_urlsafe(32)
                with LOCK:
                    for old in list(SESSIONS):
                        if SESSIONS[old] < time.time(): del SESSIONS[old]
                    SESSIONS[token] = time.time()+3600
                secure = '; Secure' if os.environ.get('COOKIE_SECURE','1') == '1' else ''
                return self.send(200,{'ok':True},cookie=f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600{secure}')
            if path == '/api/logout':
                for part in self.headers.get('Cookie','').split(';'):
                    if part.strip().startswith('session='):
                        with LOCK: SESSIONS.pop(part.strip()[8:],None)
                return self.send(200,{'ok':True},cookie='session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            if path == '/api/reports':
                if not self.throttle('submit',15): return self.send(429,{'error':'提交过于频繁，请稍后再试'})
                fields = {}
                for key,limit in [('kind',20),('building',30),('floor',10),('side',10),('machine',20),('occurred',30),('description',1000),('reporter_building',30),('room',20)]:
                    value = d.get(key,'')
                    if not isinstance(value,str) or len(value.strip()) > limit: raise ValueError('字段过长或格式无效')
                    fields[key] = value.strip()
                if any(not fields[k] for k in ('building','floor','occurred','description','reporter_building','room')): raise ValueError('请填写所有必填字段')
                if fields['kind'] not in ('厕所吸烟','洗衣机使用问题'): raise ValueError('请选择问题类型')
                if fields['kind'] == '洗衣机使用问题' and fields['side'] not in ('东侧','西侧'): raise ValueError('请选择洗衣机方位')
                if fields['kind'] == '厕所吸烟': fields['side'] = fields['machine'] = ''
                datetime.fromisoformat(fields['occurred'])
                if d.get('consent') is not True: raise ValueError('请确认照片脱敏及信息使用说明')
                photo = sanitize_png(base64.b64decode(d.get('photo',''),validate=True))
                rid = secrets.token_hex(12)
                with connect() as c:
                    c.execute('INSERT INTO reports (id,'+','.join(fields)+',image) VALUES ('+','.join('?' for _ in range(len(fields)+2))+')',(rid,*fields.values(),photo))
                return self.send(201,{'id':rid,'message':'提交成功，审核后公开'})
            if path in ('/api/moderate','/api/delete'):
                if not self.admin(): return self.send(401,{'error':'请先登录'})
                with connect() as c:
                    if path == '/api/delete': cursor = c.execute('DELETE FROM reports WHERE id=?',(d.get('id'),))
                    else:
                        if d.get('status') not in ('待核实','已发布','处理中','已处理','已驳回'): raise ValueError('状态无效')
                        if d['status'] in ('已发布','处理中','已处理') and d.get('reviewed') is not True: raise ValueError('请先确认隐私和内容审核')
                        cursor = c.execute('UPDATE reports SET status=? WHERE id=?',(d['status'],d.get('id')))
                    if not cursor.rowcount: return self.send(404,{'error':'事件不存在'})
                return self.send(200,{'ok':True})
            return self.send(404,{'error':'接口不存在'})
        except (ValueError,TypeError,KeyError,struct.error,zlib.error):
            return self.send(400,{'error':'提交无效，请检查必填字段、照片与确认项'})

init()
if __name__ == '__main__':
    if len(PASSWORD) < 16: raise SystemExit('Set ADMIN_PASSWORD to at least 16 characters before starting.')
    ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8000'))),Handler).serve_forever()
