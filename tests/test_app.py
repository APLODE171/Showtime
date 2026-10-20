import base64, io, http.client, importlib.util, json, os, struct, tempfile, threading, unittest, zlib
from pathlib import Path

TMP = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = TMP.name
os.environ['ADMIN_PASSWORD'] = 'test-password-long-enough'
os.environ['COOKIE_SECURE'] = '0'
spec = importlib.util.spec_from_file_location('app', Path(__file__).parents[1]/'server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

def chunk(k,b): return struct.pack('>I',len(b))+k+b+struct.pack('>I',zlib.crc32(k+b)&0xffffffff)
def png(): return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'tEXt',b'GPS\x00private-location')+chunk(b'IDAT',zlib.compress(b'\0\xff\0\0'))+chunk(b'IEND',b'')

class TestApp(unittest.TestCase):
    def request(self,path,data=None,cookie='',extra=None):
        body=b'' if data is None else json.dumps(data).encode()
        method='GET' if data is None else 'POST'
        headers={'Content-Type':'application/json','Cookie':cookie,'Content-Length':str(len(body)),**(extra or {})}
        request=(method+' '+path+' HTTP/1.0\r\n'+''.join(k+': '+v+'\r\n' for k,v in headers.items())+'\r\n').encode()+body
        class Connection:
            def __init__(self):self.output=io.BytesIO()
            def makefile(self,*args):return io.BytesIO(request)
            def sendall(self,b):self.output.write(b)
        connection=Connection()
        app.Handler(connection,('127.0.0.1',1234),None)
        head,body=connection.output.getvalue().split(b'\r\n\r\n',1)
        lines=head.decode().split('\r\n')
        return int(lines[0].split()[1]),body,dict(line.split(': ',1) for line in lines[1:])
    def test_workflow_and_privacy(self):
        d=dict(kind='洗衣机使用问题',building='3栋',floor='2层',side='东侧',machine='1',occurred='2026-10-20T12:00',description='请依据现场规则核实',reporter_building='私密楼栋',room='私密宿舍',consent=True,photo=base64.b64encode(png()).decode())
        status,body,_=self.request('/api/reports',d);self.assertEqual(status,201)
        rid=json.loads(body)['id']
        self.assertEqual(self.request('/api/photos/'+rid)[0],404)
        self.assertEqual(self.request('/api/admin')[0],401)
        self.assertEqual(self.request('/api/moderate',dict(id=rid,status='已发布',reviewed=True))[0],401)
        status,body,headers=self.request('/api/login',{'password':os.environ['ADMIN_PASSWORD']});self.assertEqual(status,200)
        cookie=headers['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertIn('私密宿舍',self.request('/api/admin',cookie=cookie)[1].decode())
        self.assertEqual(self.request('/api/moderate',dict(id=rid,status='已发布'),cookie)[0],400)
        self.assertEqual(self.request('/api/moderate',dict(id=rid,status='已发布',reviewed=True),cookie)[0],200)
        public=json.loads(self.request('/api/reports')[1]);self.assertEqual(len(public),1)
        self.assertNotIn('room',public[0]);self.assertNotIn('reporter_building',public[0]);self.assertNotIn('私密',json.dumps(public,ensure_ascii=False))
        status,photo,_=self.request('/api/photos/'+rid);self.assertEqual(status,200);self.assertNotIn(b'private-location',photo)
        self.assertEqual(self.request('/api/moderate',dict(id=rid,status='待核实'),cookie)[0],200)
        self.assertEqual(self.request('/api/photos/'+rid)[0],404)
        self.assertEqual(self.request('/api/delete',dict(id=rid),cookie)[0],200)
        self.assertEqual(self.request('/api/photos/'+rid,cookie=cookie)[0],404)
        self.request('/api/logout',{},cookie)
        self.assertEqual(self.request('/api/admin',cookie=cookie)[0],401)
    def test_invalid_and_cross_origin(self):
        self.assertEqual(self.request('/api/reports',{})[0],400)
        self.assertEqual(self.request('/api/login',{'password':'wrong'})[0],401)
        self.assertEqual(self.request('/api/login',{'password':'wrong'},extra={'Sec-Fetch-Site':'cross-site'})[0],403)
        self.assertEqual(self.request('/data/reports.sqlite3')[0],404)
        for bad in (b'not a photo',png()[:-3],png()+b'extra'):
            with self.assertRaises(ValueError):app.sanitize_png(bad)
    def test_metadata_removed(self):
        clean=app.sanitize_png(png());self.assertNotIn(b'tEXt',clean);self.assertNotIn(b'GPS',clean)
        self.assertEqual(app.sanitize_png(clean),clean)

if __name__=='__main__':unittest.main()
