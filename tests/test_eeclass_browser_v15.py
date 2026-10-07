import importlib.util
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('downloader', Path(__file__).resolve().parents[1] / 'eeclass_video_downloader.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class BrowserSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.owned = self.root / 'app' / 'browser-cache'
        self.shared = self.root / 'shared'
        self.installed = None
        self.manager = Mock()
        self.module = types.ModuleType('selenium.webdriver.common.selenium_manager')
        self.module.SeleniumManager = Mock(return_value=self.manager)
        self.logs = []
        for context in (
            patch.object(m, 'automation_directory', return_value=self.root / 'app'),
            patch.object(m, 'selenium_cache_directory', return_value=self.shared),
            patch.object(m, 'firefox_executable', side_effect=lambda: self.installed),
            patch.object(m, 'executable_version', side_effect=self.version),
            patch.dict(m.sys.modules, {'selenium.webdriver.common.selenium_manager': self.module}),
        ):
            context.start()
            self.addCleanup(context.stop)

    def file(self, path, content=b'healthy'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path.resolve())

    def browser(self, cache, version='150.0', content=b'healthy'):
        suffix = 'firefox.exe' if m.os.name == 'nt' else ('Firefox.app/Contents/MacOS/firefox' if m.sys.platform == 'darwin' else 'firefox')
        return self.file(cache / 'firefox' / 'platform' / version / suffix, content)

    def version(self, path, product):
        if isinstance(path, (str, Path)) and Path(path).is_file() and Path(path).read_bytes() != b'broken':
            return '150.0' if product == 'Firefox' else '0.36.0'
        return None

    def download(self, args):
        if '--offline' in args:
            raise RuntimeError('not cached')
        browser = self.installed or self.browser(self.owned)
        driver = self.file(self.owned / 'geckodriver' / 'driver')
        return {'browser_path': browser, 'driver_path': driver}

    def prepare(self):
        return m.prepare_firefox(self.logs.append)

    def test_first_download_then_second_start_has_no_manager_call(self):
        self.manager.binary_paths.side_effect = self.download
        first = self.prepare()
        self.assertEqual(self.manager.binary_paths.call_count, 1)
        self.manager.binary_paths.reset_mock()
        self.assertEqual(self.prepare(), first)
        self.manager.binary_paths.assert_not_called()
        self.assertTrue(any('不重新下載' in text for text in self.logs))
        self.assertEqual(len(m.cached_firefoxes(self.owned)), 1)

    def test_installed_firefox_prevents_browser_download(self):
        self.installed = self.file(self.root / 'system' / 'firefox')
        self.manager.binary_paths.side_effect = self.download
        browser, _ = self.prepare()
        self.assertEqual(browser, self.installed)
        for call in self.manager.binary_paths.call_args_list:
            self.assertIn('--avoid-browser-download', call.args[0])
            self.assertIn(self.installed, call.args[0])
        self.assertEqual(m.cached_firefoxes(self.owned), [])

    def test_shared_browser_and_driver_reused_without_download(self):
        browser = self.browser(self.shared)
        driver = self.file(self.shared / 'geckodriver' / 'driver')
        def cached(args):
            self.assertIn('--offline', args)
            if str(self.shared) not in args:
                raise RuntimeError('not found')
            return {'browser_path': browser, 'driver_path': driver}
        self.manager.binary_paths.side_effect = cached
        self.assertEqual(self.prepare(), (browser, driver))
        self.assertEqual(m.cached_firefoxes(self.owned), [])

    def test_new_system_install_preferred_over_previous_download(self):
        self.manager.binary_paths.side_effect = self.download
        self.prepare()
        self.installed = self.file(self.root / 'system' / 'firefox')
        self.assertEqual(self.prepare()[0], self.installed)

    def test_changed_browser_invalidates_record(self):
        self.manager.binary_paths.side_effect = self.download
        browser, _ = self.prepare()
        Path(browser).write_bytes(b'updated browser')
        self.manager.binary_paths.reset_mock()
        self.prepare()
        self.assertGreater(self.manager.binary_paths.call_count, 0)
        for call in self.manager.binary_paths.call_args_list:
            self.assertIn('--avoid-browser-download', call.args[0])

    def test_missing_driver_does_not_redownload_browser(self):
        self.manager.binary_paths.side_effect = self.download
        browser, driver = self.prepare()
        Path(driver).unlink()
        self.manager.binary_paths.reset_mock()
        self.assertEqual(self.prepare()[0], browser)
        for call in self.manager.binary_paths.call_args_list:
            self.assertIn('--avoid-browser-download', call.args[0])

    def test_missing_browser_can_download_again(self):
        self.manager.binary_paths.side_effect = self.download
        browser, _ = self.prepare()
        Path(browser).unlink()
        self.manager.binary_paths.reset_mock()
        self.assertEqual(self.prepare()[0], browser)
        self.assertNotIn('--avoid-browser-download', self.manager.binary_paths.call_args.args[0])

    def test_failed_download_does_not_write_ready_record(self):
        self.manager.binary_paths.side_effect = RuntimeError('network failed')
        with self.assertRaisesRegex(RuntimeError, '準備失敗'):
            self.prepare()
        self.assertFalse((self.root / 'app' / 'browser-assets.json').exists())
        self.manager.binary_paths.side_effect = self.download
        self.prepare()
        self.assertTrue((self.root / 'app' / 'browser-assets.json').is_file())

    def test_browser_download_completed_but_driver_failed_is_reused(self):
        def interrupted(args):
            self.browser(self.owned)
            raise RuntimeError('driver download failed')
        self.manager.binary_paths.side_effect = interrupted
        with self.assertRaises(RuntimeError):
            self.prepare()
        self.manager.binary_paths.side_effect = self.download
        self.manager.binary_paths.reset_mock()
        self.prepare()
        for call in self.manager.binary_paths.call_args_list:
            self.assertIn('--avoid-browser-download', call.args[0])

    def test_broken_owned_browser_repaired_without_removing_shared_data(self):
        broken = Path(self.browser(self.owned, '149.0', b'broken'))
        shared = Path(self.browser(self.shared, '149.0', b'broken'))
        self.manager.binary_paths.side_effect = self.download
        self.prepare()
        self.assertFalse(broken.exists())
        self.assertTrue(shared.exists())
        self.assertEqual(len(m.cached_firefoxes(self.owned)), 1)

    def test_malformed_record_recovers_from_cache(self):
        state = self.root / 'app' / 'browser-assets.json'
        state.parent.mkdir(parents=True)
        state.write_text('[null]', encoding='utf-8')
        self.browser(self.owned)
        self.manager.binary_paths.side_effect = self.download
        self.prepare()
        self.assertIsInstance(json.loads(state.read_text(encoding='utf-8')), dict)

    def test_version_sort_is_numeric(self):
        old = self.browser(self.shared, '99.0')
        new = self.browser(self.shared, '150.0')
        self.assertEqual([str(p.resolve()) for p in m.cached_firefoxes(self.shared)], [new, old])

    def test_invalid_download_is_not_marked_ready(self):
        self.manager.binary_paths.return_value = {'browser_path': str(self.root / 'missing'), 'driver_path': ''}
        with self.assertRaisesRegex(RuntimeError, '驗證失敗'):
            self.prepare()
        self.assertFalse((self.root / 'app' / 'browser-assets.json').exists())

    def test_os_lock_rejects_concurrent_setup_and_releases_after_exception(self):
        with self.assertRaisesRegex(ValueError, 'fixture'):
            with m.setup_lock(self.logs.append):
                with self.assertRaisesRegex(RuntimeError, '另一個下載器'):
                    with m.setup_lock(self.logs.append, timeout=0):
                        self.fail('second setup acquired the lock')
                raise ValueError('fixture')
        with m.setup_lock(self.logs.append, timeout=0):
            pass


class ExecutableProbeTests(unittest.TestCase):
    def test_probe_checks_return_code_and_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / 'firefox'
            exe.touch()
            with patch.object(m.subprocess, 'run', return_value=Mock(returncode=0, stdout=b'Mozilla Firefox 150.0.1')):
                self.assertEqual(m.executable_version(exe, 'Firefox'), '150.0.1')
            with patch.object(m.subprocess, 'run', return_value=Mock(returncode=1, stdout=b'Mozilla Firefox 150.0.1')):
                self.assertIsNone(m.executable_version(exe, 'Firefox'))
            with patch.object(m.subprocess, 'run', side_effect=subprocess.TimeoutExpired('firefox', 15)):
                self.assertIsNone(m.executable_version(exe, 'Firefox'))


if __name__ == '__main__':
    unittest.main()
