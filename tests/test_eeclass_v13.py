import importlib.util, unittest, tempfile, sqlite3, time, io, urllib.request, urllib.response
from pathlib import Path
from unittest.mock import patch
from email.message import Message
spec=importlib.util.spec_from_file_location('downloader',str(Path(__file__).resolve().parents[1] / 'eeclass_video_downloader.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
URL='https://eeclass.nptu.edu.tw/media/doc/46710'
def make_cookie(name,value,path='/'):
 return m.Cookie(0,name,value,None,False,'eeclass.nptu.edu.tw',False,False,path,True,True,None,True,None,None,{})
class SessionTests(unittest.TestCase):
 def test_cookie_updates_on_redirect(self):
  jar=m.CookieJar();jar.set_cookie(make_cookie('session','old'));jar.set_cookie(make_cookie('courseonly','pathvalue','/course'))
  seen=[]
  class FakeHTTPS(urllib.request.HTTPSHandler):
   def https_open(self,request):
    seen.append((request.full_url,request.get_header('Cookie')))
    headers=Message()
    if request.full_url==URL:
     headers['Location']='https://eeclass.nptu.edu.tw/course/info/9375'
     headers['Set-Cookie']='session=new; Path=/; Secure; HttpOnly'
     status=302
    else:status=200
    response=urllib.response.addinfourl(io.BytesIO(b'html'),headers,request.full_url,status)
    response.msg='Found' if status==302 else 'OK'
    return response
  logs=[]
  opener=urllib.request.build_opener(FakeHTTPS(),m.EeclassRedirectHandler(logs.append),urllib.request.HTTPCookieProcessor(jar))
  with opener.open(URL) as response:self.assertEqual(response.code,200)
  self.assertEqual(seen[0][1],'session=old')
  self.assertIn('session=new',seen[1][1]);self.assertNotIn('session=old',seen[1][1])
  self.assertIn('courseonly=pathvalue',seen[1][1]);self.assertNotIn('pathvalue','\n'.join(logs))
 def test_expiry_milliseconds(self):
  with tempfile.TemporaryDirectory() as td:
   profile=Path(td);db=sqlite3.connect(profile/'cookies.sqlite');db.execute('PRAGMA user_version=16')
   db.execute('CREATE TABLE moz_cookies(host TEXT,path TEXT,name TEXT,value TEXT,expiry INTEGER,isSecure INTEGER)')
   now=int(time.time())
   for name,expires in [('expired',(now-100)*1000),('active',(now+3600)*1000)]:
    db.execute('INSERT INTO moz_cookies VALUES(?,?,?,?,?,?)',('eeclass.nptu.edu.tw','/',name,'fixture',expires,1))
   db.commit();db.close()
   with patch.object(m,'firefox_profiles',return_value=[profile]):jar,_=m.firefox_cookie_candidates(URL)[0]
   req=urllib.request.Request(URL);jar.add_cookie_header(req)
   self.assertEqual(req.get_header('Cookie'),'active=fixture')
 def test_saved_html_source(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'video.html';path.write_text('<title>影片二</title><video><source src="/sysdata/video.mp4"></video>', encoding='utf-8')
   sources,title=m.read_saved_video_page(path,URL)
   self.assertEqual(title,'影片二');self.assertEqual(sources[0]['url'],'https://eeclass.nptu.edu.tw/sysdata/video.mp4')
 def test_saved_html_invalid_and_empty(self):
  with self.assertRaises(ValueError):m.read_saved_video_page('nonexistent','https://other.invalid/')
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'course.html';path.write_text('<title>課程資訊</title>', encoding='utf-8')
   with self.assertRaisesRegex(ValueError,'Ctrl\\+U'):m.read_saved_video_page(path,URL)
 def test_course_redirect_skips_syllabus(self):
  seen=[];logs=[]
  def fetch(url,data,log=None,metadata=None):
   seen.append(url);metadata.update(url='https://eeclass.nptu.edu.tw/course/info/9375',status=200,content_type='text/html')
   return '<title>課程資訊</title><iframe src="/sys/mate/course_plan.php">'
  with patch.object(m,'firefox_cookie_candidates',return_value=[(m.CookieJar(),Path('profile'))]),patch.object(m,'fetch_page',side_effect=fetch):
   with self.assertRaises(RuntimeError):m.resolve_video_page(URL,logs.append)
  self.assertEqual(seen,[URL]);self.assertTrue(any('不將課綱' in line for line in logs))
 def test_fetch_has_processor_and_no_manual_cookie(self):
  from unittest.mock import MagicMock
  jar=m.CookieJar();jar.set_cookie(make_cookie('session','fixture'))
  response=MagicMock();response.__enter__.return_value=response
  response.read.return_value=b'html';response.headers.get_content_charset.return_value='utf-8'
  with patch.object(m,'open_eeclass_page',return_value=response) as opened:
   m.fetch_page(URL,(jar,Path('profile')))
  req=opened.call_args.args[0]
  self.assertFalse(req.has_header('Cookie'))
  self.assertIs(opened.call_args.kwargs['cookiejar'],jar)
if __name__=='__main__':unittest.main(verbosity=2)
