import importlib.util, unittest, tempfile, queue, time, types, sys
from pathlib import Path
from unittest.mock import patch, Mock
spec=importlib.util.spec_from_file_location('downloader',str(Path(__file__).resolve().parents[1] / 'eeclass_video_downloader.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
URL='https://eeclass.nptu.edu.tw/media/doc/46710'
class FakeDriver:
 def __init__(self,page='<video src="/movie.mp4"></video>',url=URL):
  self.current_url=url;self.page_source=page;self.visits=[];self.cookie_reads=0
  self.window_handles=['original'];self.switch_to=Mock()
 def get(self,url):self.visits.append(url)
 def get_cookies(self):
  self.cookie_reads+=1
  return [{'domain':'eeclass.nptu.edu.tw','path':'/','name':'session','value':'fixture','secure':True}, {'domain':'outside.invalid','name':'other','value':'omit'}]
 def execute_script(self,script):
  if script=='return navigator.userAgent;':return 'Firefox fixture'
  return [{'url':'https://eeclass.nptu.edu.tw/dynamic.mp4','width':1920,'height':1080,'title':'動態'}]
exceptions=types.ModuleType('selenium.common.exceptions')
exceptions.TimeoutException=type('TimeoutException',(Exception,),{})
class BrowserTests(unittest.TestCase):
 def test_snapshot_only_requested_document(self):
  for url in ['https://eeclass.nptu.edu.tw/course/info/9375','https://elsewhere.invalid/login','https://eeclass.nptu.edu.tw/media/doc/99999']:
   driver=FakeDriver(url=url)
   self.assertIsNone(m.media_snapshot(driver,URL));self.assertEqual(driver.cookie_reads,0)
 def test_snapshot_filters_cookie_and_resolves_url(self):
  result=m.media_snapshot(FakeDriver(),URL)
  self.assertEqual(result['sources'][0]['url'],'https://eeclass.nptu.edu.tw/movie.mp4')
  self.assertEqual(len(result['cookies']),1);self.assertEqual(result['user_agent'],'Firefox fixture')
 def test_dynamic_dom_fallback(self):
  result=m.media_snapshot(FakeDriver(page='<video></video>'),URL)
  self.assertEqual(result['sources'][0]['height'],1080)
 def test_base64_quality_has_priority(self):
  page='media={"src":[{"src":"/best.mp4","width":1920,"height":1080},{"src":"/small.mp4","width":640,"height":360}]};'
  self.assertEqual(m.media_snapshot(FakeDriver(page=page),URL)['sources'][0]['url'],'https://eeclass.nptu.edu.tw/best.mp4')
 def test_live_cookie_file_roundtrip(self):
  jar=m.browser_cookie_jar([{'domain':'eeclass.nptu.edu.tw','name':'sid','value':'live','secure':True,'httpOnly':True}, {'domain':'outside.invalid','name':'x','value':'no'}, {'domain':'eeclass.nptu.edu.tw','name':'expired','value':'no','expiry':time.time()-5}])
  self.assertEqual([c.name for c in jar],['sid'])
  from http.cookiejar import MozillaCookieJar
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'cookies.txt';jar.save(str(path),ignore_discard=True)
   loaded=MozillaCookieJar(str(path));loaded.load(ignore_discard=True)
   self.assertEqual(next(iter(loaded)).value,'live')
 def test_cookie_newline_rejected(self):
  self.assertEqual(list(m.browser_cookie_jar([{'domain':'eeclass.nptu.edu.tw','name':'sid','value':'a\ninvalid'}])),[])
 def test_live_download_command(self):
  cmd=m.build_download_command(['yt-dlp'],{'url':'https://eeclass.nptu.edu.tw/a.mp4'},URL,Path('unused'),Path('output.mp4'),cookie_file=Path('temp/cookies.txt'),user_agent='Firefox fixture')
  self.assertNotIn('--cookies-from-browser',cmd);self.assertIn('--cookies',cmd)
  self.assertEqual(cmd[cmd.index('--user-agent')+1],'Firefox fixture')
 def test_browser_immediate_success(self):
  with patch.dict(sys.modules,{'selenium.common.exceptions':exceptions}):
   driver=FakeDriver();events=[];result=m.resolve_in_browser(driver,URL,queue.Queue(),events.append)
  self.assertEqual(result['type'],'result');self.assertEqual(events,[])
 def test_browser_login_continue(self):
  commands=queue.Queue();driver=FakeDriver(url='https://eeclass.nptu.edu.tw/course/info/9375');events=[]
  def emit(event):
   events.append(event);driver.current_url=URL;commands.put({'op':'continue'})
  with patch.dict(sys.modules,{'selenium.common.exceptions':exceptions}),patch.object(m.time,'sleep'):
   result=m.resolve_in_browser(driver,URL,commands,emit)
  self.assertEqual(result['type'],'result');self.assertEqual(driver.visits,[URL,URL]);self.assertEqual(events[0]['type'],'login_required')
 def test_cancel_and_quit(self):
  for op in ['cancel','quit']:
   commands=queue.Queue();commands.put({'op':op})
   with patch.dict(sys.modules,{'selenium.common.exceptions':exceptions}):
    result=m.resolve_in_browser(FakeDriver(),URL,commands,lambda _:None)
   self.assertEqual(result,{'type':'cancelled','quit':op=='quit'})
 def test_login_timeout(self):
  with patch.dict(sys.modules,{'selenium.common.exceptions':exceptions}):
   with self.assertRaisesRegex(RuntimeError,'5 分鐘'):m.resolve_in_browser(FakeDriver(),URL,queue.Queue(),lambda _:None,timeout=-1)
 def test_setup_reuses_healthy_runtime(self):
  with tempfile.TemporaryDirectory() as td:
   runtime=Path(td)/f'runtime-{sys.version_info.major}.{sys.version_info.minor}'/('Scripts' if m.os.name == 'nt' else 'bin')/('python.exe' if m.os.name == 'nt' else 'python')
   runtime.parent.mkdir(parents=True);runtime.touch()
   with patch.object(m,'automation_directory',return_value=Path(td)),patch.object(m.subprocess,'run',return_value=Mock(returncode=0)) as run:
    result=m.automation_python(lambda _:None)
   self.assertEqual(result,runtime);self.assertEqual(run.call_count,1)
if __name__=='__main__':unittest.main(verbosity=2)
