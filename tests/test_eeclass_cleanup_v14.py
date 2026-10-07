import importlib.util, unittest, tempfile, queue, types, io, json, sys
from pathlib import Path
from unittest.mock import patch, Mock
spec=importlib.util.spec_from_file_location('downloader',str(Path(__file__).resolve().parents[1] / 'eeclass_video_downloader.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
URL='https://eeclass.nptu.edu.tw/media/doc/46710'
class ImmediateThread:
 def __init__(self,target,**kwargs):self.target=target
 def start(self):self.target()
class CleanupTests(unittest.TestCase):
 def download_case(self,code):
  with tempfile.TemporaryDirectory() as td:
   value=lambda text:types.SimpleNamespace(get=lambda:text,set=lambda x:None)
   app=types.SimpleNamespace(selected_source=lambda:{'url':'https://eeclass.nptu.edu.tw/movie.mp4','width':1920,'height':1080},busy=False,
    page_url=URL,profile=Path('profile'),output_var=value(td),name_var=value('影片'),
    browser_auth={'cookies':[{'domain':'eeclass.nptu.edu.tw','path':'/','name':'session','value':'fixture','secure':True}],'user_agent':'Firefox fixture'},
    _set_busy=lambda v:None,status_var=value(''),detail_var=value(''),percent_var=value(''),progress=Mock(),_write_log=lambda x:None,msg_queue=queue.Queue())
   cookie_paths=[]
   class Proc:
    stdout=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def wait(self):return code
   def popen(cmd,**kwargs):
    cookies=Path(cmd[cmd.index('--cookies')+1]);cookie_paths.append(cookies)
    self.assertTrue(cookies.is_file());self.assertIn('fixture',cookies.read_text(encoding='utf-8'))
    if code==0:Path(cmd[cmd.index('-o')+1]).write_bytes(b'fixture mp4')
    return Proc()
   with patch.object(m,'find_ytdlp',return_value=['yt-dlp']),patch.object(m.threading,'Thread',ImmediateThread),patch.object(m.subprocess,'Popen',side_effect=popen):
    m.EeclassDownloaderApp.download(app)
   self.assertEqual(len(cookie_paths),1);self.assertFalse(cookie_paths[0].exists());self.assertFalse(cookie_paths[0].parent.exists())
   self.assertEqual(app.msg_queue.get()[0],'done' if code==0 else 'error')
 def test_cleanup_success(self):self.download_case(0)
 def test_cleanup_failure(self):self.download_case(1)
 def test_worker_quit_closes_only_managed_driver(self):
  selenium=types.ModuleType('selenium');driver=Mock();selenium.webdriver=types.SimpleNamespace(Firefox=Mock(return_value=driver))
  options=types.ModuleType('selenium.webdriver.firefox.options');options.Options=Mock
  service=types.ModuleType('selenium.webdriver.firefox.service');service.Service=Mock
  fake_socket=Mock();fake_socket.__enter__=Mock(return_value=fake_socket);fake_socket.__exit__=Mock(return_value=False);fake_socket.getsockname.return_value=('127.0.0.1',2828)
  import socket
  with tempfile.TemporaryDirectory() as td,patch.dict(sys.modules,{'selenium':selenium,'selenium.webdriver.firefox.options':options,'selenium.webdriver.firefox.service':service}),patch.object(m,'prepare_firefox',return_value=('fixture-firefox','fixture-geckodriver')),patch.object(m,'automation_directory',return_value=Path(td)),patch.object(m,'resolve_in_browser',return_value={'type':'cancelled','quit':True}),patch.object(socket,'socket',return_value=fake_socket),patch.object(sys,'stdin',io.StringIO(json.dumps({'op':'resolve','url':URL})+'\n')),patch.object(sys,'stdout',io.StringIO()):
   m.browser_worker_main()
  driver.quit.assert_called_once()
if __name__=='__main__':unittest.main(verbosity=2)
