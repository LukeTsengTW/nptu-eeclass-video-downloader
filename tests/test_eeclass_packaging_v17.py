import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('downloader', Path(__file__).resolve().parents[1] / 'eeclass_video_downloader.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class FrozenTests(unittest.TestCase):
    def test_frozen_downloader_uses_bundled_worker_not_system_path(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'C:/App/eeClass-Downloader.exe'), patch.object(m.shutil, 'which') as which:
            self.assertEqual(m.find_ytdlp(), ['C:/App/eeClass-Downloader.exe', '--ytdlp-worker'])
        which.assert_not_called()

    def test_frozen_selenium_does_not_install_venv_or_pip(self):
        selenium = types.SimpleNamespace(__version__=m.SELENIUM_VERSION)
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'app.exe'), patch.dict(sys.modules, {'selenium': selenium}), patch.object(m.subprocess, 'run') as run, patch.object(m, 'setup_lock') as lock:
            self.assertEqual(m.automation_python(Mock()), Path('app.exe'))
        run.assert_not_called()
        lock.assert_not_called()

    def test_wrong_bundled_selenium_fails_without_runtime_install(self):
        with patch.object(sys, 'frozen', True, create=True), patch.dict(sys.modules, {'selenium': types.SimpleNamespace(__version__='0')}), patch.object(m.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'EXE'): m.automation_python(Mock())
        run.assert_not_called()

    def test_frozen_browser_command_contains_no_python_flags_or_source_path(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(m, 'automation_python', return_value=Path('app.exe')):
            self.assertEqual(m.browser_worker_command(Mock()), ['app.exe', '--browser-worker'])

    def test_source_browser_command_preserves_python_flow(self):
        with patch.object(sys, 'frozen', False, create=True), patch.object(m, 'automation_python', return_value=Path('python')):
            command = m.browser_worker_command(Mock())
        self.assertEqual(command[:2], ['python', '-u'])
        self.assertEqual(Path(command[2]).name, 'eeclass_video_downloader.py')
        self.assertEqual(command[-1], '--browser-worker')

    def test_worker_dispatch_never_opens_gui(self):
        ytdlp = types.SimpleNamespace(main=Mock())
        with patch.object(m, 'main') as gui, patch.object(m, 'browser_worker_main') as browser, patch.dict(sys.modules, {'yt_dlp': ytdlp}):
            m.entry_point(['--browser-worker'])
            browser.assert_called_once()
            m.entry_point(['--ytdlp-worker', '--version'])
            ytdlp.main.assert_called_once_with(['--version'])
        gui.assert_not_called()

    def test_default_dispatch_opens_gui_once(self):
        with patch.object(m, 'main') as gui:
            m.entry_point([])
        gui.assert_called_once()


if __name__ == '__main__': unittest.main()
