from pathlib import Path
import importlib.util, unittest, ssl, urllib.error, urllib.request
from unittest.mock import patch, Mock
spec=importlib.util.spec_from_file_location('downloader',str(Path(__file__).resolve().parents[1] / 'eeclass_video_downloader.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class TLS(unittest.TestCase):
 def setUp(self):
  self.req=urllib.request.Request('https://eeclass.nptu.edu.tw/media/doc/46710')
 def certificate_error(self,message):
  reason=ssl.SSLCertVerificationError(1,message);reason.verify_message=message
  return urllib.error.URLError(reason)
 def run_attempts(self,side_effect):
  contexts=[]
  opener=Mock();opener.open.side_effect=side_effect
  def build(handler,redirect):
   ctx=handler._context
   contexts.append((ctx.verify_flags,ctx.verify_mode,ctx.check_hostname))
   return opener
  ctx=ssl.create_default_context();ctx.verify_flags |= ssl.VERIFY_X509_STRICT
  logs=[]
  with patch.object(m.ssl,'create_default_context',return_value=ctx),patch.object(m.urllib.request,'build_opener',side_effect=build):
   result=m.open_eeclass_page(self.req,log=logs.append)
  return result,contexts,logs
 def test_missing_ski_retries_with_verification(self):
  response=object()
  result,contexts,logs=self.run_attempts([self.certificate_error('Missing Subject Key Identifier'),response])
  self.assertIs(result,response);self.assertEqual(len(contexts),2)
  before,after=contexts
  self.assertEqual(after[0],before[0] & ~ssl.VERIFY_X509_STRICT)
  for flags,mode,hostname in contexts:
   self.assertEqual(mode,ssl.CERT_REQUIRED);self.assertTrue(hostname)
  self.assertEqual(len(logs),1)
 def test_success_keeps_defaults(self):
  response=object();result,contexts,logs=self.run_attempts([response])
  self.assertEqual(len(contexts),1);self.assertTrue(contexts[0][0]&ssl.VERIFY_X509_STRICT);self.assertEqual(logs,[])
 def test_other_errors_never_retry(self):
  for msg in ['certificate has expired','Hostname mismatch','unable to get local issuer certificate','self-signed certificate']:
   with self.subTest(msg=msg):
    opener=Mock();opener.open.side_effect=self.certificate_error(msg)
    with patch.object(m.urllib.request,'build_opener',return_value=opener):
     with self.assertRaises(urllib.error.URLError):m.open_eeclass_page(self.req)
    self.assertEqual(opener.open.call_count,1)
 def test_retry_error_propagates(self):
  with self.assertRaises(urllib.error.URLError):
   self.run_attempts([self.certificate_error('Missing Subject Key Identifier'),self.certificate_error('certificate has expired')])
 def test_without_strict_does_not_retry(self):
  ctx=ssl.create_default_context();ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
  opener=Mock();opener.open.side_effect=self.certificate_error('Missing Subject Key Identifier')
  with patch.object(m.ssl,'create_default_context',return_value=ctx),patch.object(m.urllib.request,'build_opener',return_value=opener):
   with self.assertRaises(urllib.error.URLError):m.open_eeclass_page(self.req)
  self.assertEqual(opener.open.call_count,1)
 def test_wrong_origin_not_requested(self):
  with patch.object(m.urllib.request,'build_opener') as build:
   with self.assertRaises(urllib.error.URLError):m.open_eeclass_page(urllib.request.Request('https://example.org/'))
   build.assert_not_called()
 def test_redirect_scope(self):
  handler=m.EeclassRedirectHandler()
  for url in ['http://eeclass.nptu.edu.tw/login','https://example.org/','https://eeclass.nptu.edu.tw:444/']:
   with self.subTest(url=url),self.assertRaises(urllib.error.URLError):
    handler.redirect_request(self.req,None,302,'Found',{},url)
  redirected=handler.redirect_request(self.req,None,302,'Found',{},'https://eeclass.nptu.edu.tw/login')
  self.assertEqual(redirected.host,'eeclass.nptu.edu.tw')
 def test_fetch_page_reads_response_after_retry(self):
  response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
  response.read.return_value='測試頁面'.encode();response.headers.get_content_charset.return_value='utf-8'
  with patch.object(m,'open_eeclass_page',return_value=response) as fetch:
   text=m.fetch_page(self.req.full_url,('synthetic=value',None))
  self.assertEqual(text,'測試頁面');self.assertEqual(fetch.call_count,1)
if __name__=='__main__':unittest.main(verbosity=2)
