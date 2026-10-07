import base64, json, importlib.util, unittest, tempfile, sqlite3, os, time, ast
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('downloader', str(Path(__file__).resolve().parents[1] / 'eeclass_video_downloader.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
URL='https://eeclass.nptu.edu.tw/media/doc/46710'
class ParserTests(unittest.TestCase):
 def test_source_tags_unquoted_and_entities(self):
  found=m.extract_sources('<video><source src=/a.mp4><source src="/b.mp4?a=1&amp;b=2"></video>')
  self.assertEqual({s['url'] for s in found},{'/a.mp4','/b.mp4?a=1&b=2'})
 def test_base64_nested_spaces_and_padding(self):
  obj={'data':{'title':'課程','sources':[{'file':'/f.mp4','width':1920,'height':1080}]}}
  payload=base64.b64encode(json.dumps(obj).encode()).decode().rstrip('=')
  page='media=JSON.parse(atob ( "'+payload[:12]+'\n'+payload[12:]+'" ));'
  found=m.extract_sources(page)
  self.assertEqual(found[0]['width'],1920);self.assertEqual(found[0]['title'],'課程')
 def test_direct_json_and_string_sources(self):
  page='media = {"src":["/a.mp4", {"src":"/b.mp4","size":{"width":"1920","height":"1080"}}]};'
  self.assertEqual(m.extract_sources(page)[0]['url'],'/b.mp4')
 def test_json_script(self):
  page='<script type="application/json">{"player":{"src":[{"url":"/a.mp4"}]}}</script>'
  self.assertEqual(m.extract_sources(page)[0]['url'],'/a.mp4')
 def test_merge_retains_resolution(self):
  page='media={"src":[{"src":"/a.mp4","size":{"width":1920,"height":1080}}]};<video src="/a.mp4">'
  found=m.extract_sources(page);self.assertEqual(len(found),1);self.assertEqual(found[0]['height'],1080)
 def test_second_profile_success(self):
  profiles=[('secret1',Path('old')),('secret2',Path('active'))];logs=[]
  def fetch(url,cookies,log=None,metadata=None):
   metadata.update(url=url,status=200,content_type='text/html')
   return '<input type=password>' if cookies[1].name=='old' else '<title>影片</title><source src="/a.mp4">'
  with patch.object(m,'firefox_cookie_candidates',return_value=profiles),patch.object(m,'fetch_page',side_effect=fetch):
   result=m.resolve_video_page(URL,logs.append)
  self.assertEqual(result[2],Path('active'));self.assertTrue(result[0][0]['url'].endswith('/a.mp4'))
  self.assertNotIn('secret1','\n'.join(logs));self.assertNotIn('secret2','\n'.join(logs))
 def test_iframe_and_skip_external(self):
  profiles=[('cookie',Path('active'))];seen=[]
  def fetch(url,cookies,log=None,metadata=None):
   seen.append(url);metadata.update(url=url,status=200,content_type='text/html')
   if url==URL:return '<iframe src="https://other.invalid/frame"></iframe><iframe src="/media/player/42"></iframe>'
   return '<video src="video.mp4"></video>'
  with patch.object(m,'firefox_cookie_candidates',return_value=profiles),patch.object(m,'fetch_page',side_effect=fetch):
   result=m.resolve_video_page(URL,lambda _:None)
  self.assertEqual(len(seen),2);self.assertTrue(all('other.invalid' not in url for url in seen))
  self.assertEqual(result[0][0]['url'],'https://eeclass.nptu.edu.tw/media/player/video.mp4')
 def test_failure_diagnostics(self):
  logs=[]
  def fetch(url,cookies,log=None,metadata=None):
   metadata.update(url=url,status=200,content_type='text/html')
   return '<title>Login</title><input type=password>'
  with patch.object(m,'firefox_cookie_candidates',return_value=[('secret',Path('active'))]),patch.object(m,'fetch_page',side_effect=fetch):
   with self.assertRaisesRegex(RuntimeError,'複製紀錄'):m.resolve_video_page(URL,logs.append)
  self.assertIn('疑似登入頁=1','\n'.join(logs));self.assertNotIn('secret','\n'.join(logs))
 def test_profile_ini_external(self):
  with tempfile.TemporaryDirectory() as td:
   base=Path(td)/'Mozilla'/'Firefox';base.mkdir(parents=True)
   inside=base/'Profiles'/'regular';inside.mkdir(parents=True);(inside/'cookies.sqlite').touch()
   outside=Path(td)/'custom';outside.mkdir();(outside/'cookies.sqlite').touch()
   (base/'profiles.ini').write_text(f'[Profile0]\nIsRelative=0\nPath={outside}\n')
   with patch.dict(os.environ,{'APPDATA':td}):profiles=m.firefox_profiles()
   self.assertEqual(set(profiles),{inside.resolve(),outside.resolve()})
 def test_live_wal_and_cookie_paths(self):
  with tempfile.TemporaryDirectory() as td:
   profile=Path(td);db=sqlite3.connect(profile/'cookies.sqlite')
   db.execute('PRAGMA journal_mode=WAL')
   db.execute('CREATE TABLE moz_cookies(host TEXT,path TEXT,name TEXT,value TEXT,expiry INTEGER,isSecure INTEGER)')
   rows=[('eeclass.nptu.edu.tw','/','session','fresh',int(time.time())+3600,1),('eeclass.nptu.edu.tw','/media/do','wrongpath','x',0,1),('nptu.edu.tw','/','hostonly','x',0,1)]
   db.executemany('INSERT INTO moz_cookies VALUES(?,?,?,?,?,?)',rows);db.commit()
   with patch.object(m,'firefox_profiles',return_value=[profile]):candidates=m.firefox_cookie_candidates(URL)
   request=m.urllib.request.Request(URL);candidates[0][0].add_cookie_header(request)
   self.assertEqual(request.get_header('Cookie'),'session=fresh');db.close()
 def test_unsupported_scheme_and_non_mp4(self):
  self.assertEqual(m.extract_sources('<video src="javascript:a.mp4"><source src="blob:abc"><source src="/a.m3u8">'),[])
 def test_python310_grammar(self):
  ast.parse(Path(str(Path(__file__).resolve().parents[1] / 'eeclass_video_downloader.py')).read_text(encoding='utf-8'),feature_version=(3,10))
if __name__=='__main__':unittest.main(verbosity=2)
