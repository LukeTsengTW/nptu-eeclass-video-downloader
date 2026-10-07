"""Exercise the actual frozen EXE without Python or downloaders on PATH."""
import functools
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


def main():
    exe = Path(sys.argv[1]).resolve()
    if sys.platform != 'win32':
        raise SystemExit('This smoke test requires the built Windows executable.')
    with tempfile.TemporaryDirectory(prefix='eeclass smoke ') as temp:
        root = Path(temp)
        env = dict(os.environ)
        env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
        env['LOCALAPPDATA'] = str(root / 'appdata')
        env['PYTHONPATH'] = ''
        env.pop('PYTHONHOME', None)
        env.pop('SE_MANAGER_PATH', None)
        def run(args, input=None):
            result = subprocess.run([str(exe), *args], cwd=root, env=env, input=input,
                                    capture_output=True, text=True, encoding='utf-8', timeout=600,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError(f'{args}: exit {result.returncode}\n{result.stdout}\n{result.stderr}')
            return result.stdout
        report = json.loads(run(['--self-test']).strip().splitlines()[-1])
        assert report['gui'] == 'ok'
        assert run(['--ytdlp-worker', '--version']).strip() == report['yt_dlp']
        run(['--browser-worker'], input='{"op":"quit"}\n')
        # Manager execution is checked inside --self-test, before onefile cleanup.
        assert not (root / 'appdata' / 'NPTUeeClassDownloader').exists(), 'EXE attempted runtime installation'
        browser = json.loads(run(['--self-test-browser']).strip().splitlines()[-1])
        assert browser == {'firefox': 'ok', 'webdriver': 'ok'}
        data = b'\x00\x00\x00\x18ftypmp42' + b'fixture video bytes' * 8192
        (root / 'fixture.mp4').write_bytes(data)
        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_): pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            target = root / 'download result.mp4'
            run(['--ytdlp-worker', '--ignore-config', '--proxy', '', '--no-playlist', '-o', str(target),
                 f'http://127.0.0.1:{server.server_port}/fixture.mp4'])
            assert target.read_bytes() == data, 'Downloaded bytes differ'
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)
        print(json.dumps({'gui': 'ok', 'browser_worker': 'ok', 'firefox': 'ok', 'yt_dlp': report['yt_dlp'],
                          'local_download': 'ok', 'external_python_required': False}))


if __name__ == '__main__':
    main()
