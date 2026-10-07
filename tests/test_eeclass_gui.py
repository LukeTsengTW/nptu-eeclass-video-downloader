import base64, json, importlib.util, unittest, tempfile, sqlite3, os, time
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('downloader', str(Path(__file__).resolve().parents[1] / 'eeclass_video_downloader.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_quality_sort_deduplicate(self):
  src=[{'src':'https://eeclass.nptu.edu.tw/hd.mp4','size':{'width':1280,'height':720}}, {'src':'https://eeclass.nptu.edu.tw/full.mp4','size':{'width':1920,'height':1080}}, {'src':'https://eeclass.nptu.edu.tw/hd.mp4','size':{'width':1280,'height':720}}]
  html="media = JSON.parse(atob('"+base64.b64encode(json.dumps({'src':src}).encode()).decode()+"'))"
  found=m.extract_sources(html)
  self.assertEqual(len(found),2);self.assertEqual(found[0]['width'],1920)
 def test_malformed_payload_and_size(self):
  data=base64.b64encode(json.dumps({'src':[{'src':'/video.mp4','size':'unknown'}]}).encode()).decode()
  found=m.extract_sources("atob('bad') atob('"+data+"')")
  self.assertEqual(found[0]['width'],0)
 def test_fallback(self):
  found=m.extract_sources('<video src="/test.mp4?a=1&amp;b=2">')
  self.assertEqual(found[0]['url'],'/test.mp4?a=1&b=2')
 def test_title(self):
  self.assertEqual(m.display_title([{'title':'eeclass_video'}],'<title>測試 &amp; 課程</title>','https://eeclass.nptu.edu.tw/media/doc/1'),'測試 & 課程')
 def test_progress(self):
  percentage,detail=m.parse_progress('EECLASS_PROGRESS|1048576|2097152|NA|524288|2\n')
  self.assertEqual(percentage,50);self.assertIn('0.5 MB/s',detail);self.assertIn('00:02',detail)
 def test_unknown_progress(self):
  self.assertEqual(m.parse_progress('EECLASS_PROGRESS|100|NA|NA|NA|NA')[0],None)
  self.assertIsNone(m.parse_progress('irrelevant'))
  self.assertIsNone(m.parse_progress('EECLASS_PROGRESS|10'))
 def test_command(self):
  cmd=m.build_download_command(['yt-dlp'],{'url':'https://eeclass.nptu.edu.tw/test.mp4'},'https://eeclass.nptu.edu.tw/media/doc/3',Path('C:/Profile name'),Path('100% course.mp4'))
  self.assertEqual(cmd[cmd.index('--cookies-from-browser')+1],'firefox:'+str(Path('C:/Profile name')))
  self.assertEqual(cmd[cmd.index('-o')+1],'100%% course.mp4')
  self.assertIn('--no-overwrites',cmd)
 def test_cookie_filtering(self):
  with tempfile.TemporaryDirectory() as td:
   profile=Path(td)
   db=sqlite3.connect(profile/'cookies.sqlite')
   db.execute('CREATE TABLE moz_cookies(host TEXT,path TEXT,name TEXT,value TEXT,expiry INTEGER,isSecure INTEGER)')
   now=int(time.time())
   db.executemany('INSERT INTO moz_cookies VALUES(?,?,?,?,?,?)', [('eeclass.nptu.edu.tw','/','session','synthetic',now+3600,1),('elsewhere.invalid','/','other','never',now+3600,0),('eeclass.nptu.edu.tw','/','old','expired',now-10,1)])
   db.commit();db.close()
   with patch.object(m,'firefox_profiles',return_value=[profile]):
    cookies,p=m.read_firefox_cookies_for('https://eeclass.nptu.edu.tw/media/doc/1')
   request=m.urllib.request.Request('https://eeclass.nptu.edu.tw/media/doc/1');cookies.add_cookie_header(request)
   self.assertEqual(request.get_header('Cookie'),'session=synthetic');self.assertEqual(p,profile)
if __name__=='__main__':unittest.main(verbosity=2)
