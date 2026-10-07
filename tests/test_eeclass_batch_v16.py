import importlib.util
import queue
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('downloader', Path(__file__).resolve().parents[1] / 'eeclass_video_downloader.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
URL = 'https://eeclass.nptu.edu.tw/media/doc/'


class Variable:
    def __init__(self, value=''):
        self.value = value
        self.callbacks = {}
    def get(self): return self.value
    def set(self, value):
        self.value = value
        for callback in list(self.callbacks.values()): callback()
    def trace_add(self, mode, callback):
        key = str(len(self.callbacks))
        self.callbacks[key] = callback
        return key
    def trace_remove(self, mode, key): del self.callbacks[key]


class Widget:
    def __init__(self, *args, **kwargs):
        self.options = kwargs
        self.index = -1
        self.rows = {}
    def configure(self, **kwargs): self.options.update(kwargs)
    def __setitem__(self, key, value): self.options[key] = value
    def __getitem__(self, key): return self.options[key]
    def set(self, value): self.value = value
    def current(self, index=None):
        if index is not None: self.index = index
        return self.index
    def insert(self, parent, position, iid, values): self.rows[iid] = values
    def item(self, iid, values): self.rows[iid] = values
    def get_children(self): return list(self.rows)
    def delete(self, *ids):
        for iid in ids: self.rows.pop(iid)
    def pack(self, **kwargs): pass
    def pack_forget(self): pass
    def grid(self, **kwargs): pass
    def grid_remove(self): pass
    def start(self, value): pass
    def stop(self): pass
    def see(self, value): pass
    def focus_set(self): pass
    def bind(self, *args): pass
    def destroy(self): self.destroyed = True


class ImmediateThread:
    def __init__(self, target, **kwargs): self.target = target
    def start(self): self.target()


def harness(destination):
    app = types.SimpleNamespace(
        busy=False, batch_active=False, batch_jobs=[], batch_index=-1,
        batch_phase='idle', batch_stop_requested=False, operation_id=0,
        browser_resolving=False, closing=False, sources=[], page_url='', profile=None,
        browser_auth=None, msg_queue=queue.Queue(), url_rows=[], pending=[], logs=[],
        start_browser_worker=Mock(), send_browser_command=Mock(),
    )
    for name in ('url', 'name', 'quality', 'status', 'detail', 'percent', 'video'):
        setattr(app, name + '_var', Variable())
    app.output_var = Variable(str(destination))
    app.url_vars = [app.url_var]
    for name in ('url_entry', 'urls_frame', 'add_url_btn', 'resolve_btn', 'import_btn',
                 'name_entry', 'output_entry', 'folder_btn', 'download_btn', 'copy_btn',
                 'quality_box', 'continue_btn', 'cancel_btn', 'browser_actions', 'progress',
                 'batch_table', 'batch_frame', 'batch_stop_btn'):
        setattr(app, name, Widget())
    app.after = lambda delay, callback: app.pending.append(callback)
    app._write_log = app.logs.append
    for name in ('resolve', '_resolve_url', '_set_busy', '_url_changed', 'start_batch',
                 'stop_batch', '_next_batch_item', '_finish_batch_item', '_batch_status',
                 '_drain_queue', 'download', 'selected_source', 'add_url_row', 'remove_url_row'):
        setattr(app, name, types.MethodType(getattr(m.EeclassDownloaderApp, name), app))
    return app


class InputTests(unittest.TestCase):
    def test_first_required_even_when_later_filled(self):
        for values in ([], [''], ['  ', URL + '2']):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, '第一個'):
                m.collect_video_urls(values)

    def test_blank_optional_rows_ignored_and_order_preserved(self):
        self.assertEqual(m.collect_video_urls([' ' + URL + '1 ', '', '\t', URL + '3']), [URL + '1', URL + '3'])

    def test_bad_optional_url_reports_original_row_number(self):
        with self.assertRaisesRegex(ValueError, '第 3 個'):
            m.collect_video_urls([URL + '1', '', 'https://outside.invalid'])

    def test_repeated_urls_remain_explicit_jobs(self):
        self.assertEqual(len(m.collect_video_urls([URL + '1', URL + '1'])), 2)

    def test_unsafe_origin_rejected(self):
        for url in ['http://eeclass.nptu.edu.tw/', 'https://u:p@eeclass.nptu.edu.tw/', 'https://eeclass.nptu.edu.tw:444/', 'https://eeclass.nptu.edu.tw:bad/']:
            with self.subTest(url=url), self.assertRaises(ValueError): m.collect_video_urls([url])

    def test_plus_adds_blank_row_and_first_cannot_be_removed(self):
        app = harness('.')
        with patch.object(m.tk, 'StringVar', Variable), patch.object(m.ttk, 'Frame', Widget), patch.object(m.ttk, 'Entry', Widget), patch.object(m.ttk, 'Label', Widget), patch.object(m.ttk, 'Button', Widget):
            app.add_url_row(first=True)
            app.add_url_row()
            app.add_url_row()
            self.assertEqual([v.get() for v in app.url_vars], ['', '', ''])
            app.remove_url_row(app.url_var)
            self.assertEqual(len(app.url_vars), 3)
            removed = app.url_vars[1]
            app.remove_url_row(removed)
            self.assertEqual(len(app.url_vars), 2)
            self.assertEqual(app.url_rows[1][2].options['text'], '2')
            self.assertEqual(removed.callbacks, {})
            app.busy = True
            app.add_url_row()
            self.assertEqual(len(app.url_vars), 2)

    def test_single_with_blank_extras_uses_manual_quality_flow(self):
        app = harness('.')
        app.url_var.set(URL + '1')
        app.url_vars += [Variable(' '), Variable('')]
        app._resolve_url = Mock()
        app.start_batch = Mock()
        app.resolve()
        app._resolve_url.assert_called_once_with(URL + '1')
        app.start_batch.assert_not_called()

    def test_invalid_batch_does_not_start_any_work(self):
        app = harness('.')
        app.url_var.set(URL + '1')
        app.url_vars.append(Variable('invalid'))
        app.start_batch = Mock()
        with patch.object(m.messagebox, 'showerror') as error:
            app.resolve()
        error.assert_called_once()
        app.start_batch.assert_not_called()

    def test_existing_file_and_partial_are_not_overwritten_or_resumed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'lesson.mp4').touch()
            (root / 'lesson (2).mp4.part').touch()
            self.assertEqual(m.download_output_path(root, 'lesson', {}).name, 'lesson (3).mp4')
            self.assertEqual(m.download_output_path(root, 'CON.mp4', {}).name, '_CON.mp4')


class BatchFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = harness(self.temp.name)
        self.downloads = []
        self.resolve_fail = set()
        self.download_fail = set()
        self.app.send_browser_command.side_effect = self.browser_reply
        for context in (
            patch.object(m, 'find_ytdlp', return_value=['yt-dlp']),
            patch.object(m.threading, 'Thread', ImmediateThread),
            patch.object(m.subprocess, 'Popen', side_effect=self.popen),
            patch.object(m.messagebox, 'showerror'),
        ):
            context.start()
            self.addCleanup(context.stop)

    def browser_reply(self, command):
        url = command['url']
        token = command['request_id']
        if url in self.resolve_fail:
            self.app.msg_queue.put(('error', 'fixture resolve error', token))
            return
        payload = {'sources': [{'url': 'https://eeclass.nptu.edu.tw/a.mp4', 'width': 1920, 'height': 1080},
                               {'url': 'https://eeclass.nptu.edu.tw/b.mp4', 'width': 640, 'height': 360}],
                   'page_url': url, 'title': 'same title',
                   'cookies': [{'domain': 'eeclass.nptu.edu.tw', 'path': '/', 'name': 'sid', 'value': url.rsplit('/', 1)[-1], 'secure': True}],
                   'user_agent': 'fixture'}
        self.app.msg_queue.put(('browser_result', payload, token))

    def popen(self, command, **kwargs):
        page = command[command.index('--referer') + 1]
        path = Path(command[command.index('-o') + 1])
        cookie = Path(command[command.index('--cookies') + 1])
        self.downloads.append((page, path, cookie, cookie.read_text()))
        code = 1 if page in self.download_fail else 0
        if not code: path.write_bytes(b'fixture video')
        class Proc:
            stdout = ['EECLASS_PROGRESS|5|10|NA|NA|NA\n']
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def wait(self): return code
        return Proc()

    def advance(self):
        self.app._drain_queue()
        callbacks, self.app.pending = self.app.pending, []
        for callback in callbacks:
            if callback.__name__ == '_next_batch_item': callback()

    def complete(self):
        for _ in range(20):
            self.advance()
            if not self.app.batch_active: return
        self.fail('batch did not finish')

    def start(self):
        self.app.url_var.set(URL + '1')
        self.app.url_vars.extend([Variable(''), Variable(URL + '2'), Variable(' ')])
        self.app.resolve()

    def test_two_downloads_sequential_highest_quality_fresh_cookies(self):
        self.start()
        self.complete()
        self.assertEqual([job['state'] for job in self.app.batch_jobs], ['完成', '完成'])
        self.assertEqual([item[0] for item in self.downloads], [URL + '1', URL + '2'])
        self.assertEqual([item[1].name for item in self.downloads], ['001_same title_1920x1080.mp4', '002_same title_1920x1080.mp4'])
        for index, (_, path, cookie, contents) in enumerate(self.downloads, 1):
            self.assertIn('\tsid\t' + str(index), contents)
            self.assertTrue(path.exists())
            self.assertFalse(cookie.exists())
        self.assertIn('成功 2 支', self.app.detail_var.get())
        self.assertFalse(self.app.busy)

    def test_resolve_failure_continues_without_using_stale_source(self):
        self.resolve_fail.add(URL + '1')
        self.start()
        self.complete()
        self.assertEqual([job['state'] for job in self.app.batch_jobs], ['失敗', '完成'])
        self.assertEqual([item[0] for item in self.downloads], [URL + '2'])

    def test_download_failure_continues_and_cleans_cookies(self):
        self.download_fail.add(URL + '1')
        self.start()
        self.complete()
        self.assertEqual([job['state'] for job in self.app.batch_jobs], ['失敗', '完成'])
        self.assertTrue(all(not item[2].exists() for item in self.downloads))
        self.assertIn('失敗 1 支', self.app.detail_var.get())

    def test_stop_finishes_current_and_leaves_rest_undownloaded(self):
        self.start()
        self.app.stop_batch()
        self.complete()
        self.assertEqual([job['state'] for job in self.app.batch_jobs], ['完成', '未下載'])
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.app.status_var.get(), '批次已停止')

    def test_cancel_current_parse_stops_remaining(self):
        self.app.send_browser_command.side_effect = lambda command: self.app.msg_queue.put(('browser_cancelled', '', command['request_id']))
        self.start()
        self.complete()
        self.assertEqual([job['state'] for job in self.app.batch_jobs], ['已取消', '未下載'])
        self.assertEqual(self.downloads, [])

    def test_late_error_from_previous_job_ignored(self):
        self.start()
        old_id = self.app.operation_id
        self.advance()
        self.app.msg_queue.put(('error', 'late old error', old_id))
        self.complete()
        self.assertEqual([job['state'] for job in self.app.batch_jobs], ['完成', '完成'])
        self.assertFalse(any('late old error' in line for line in self.app.logs))

    def test_job_failure_preflight_does_not_hang(self):
        self.start()
        with patch.object(m, 'find_ytdlp', side_effect=RuntimeError('missing downloader')):
            self.complete()
        self.assertEqual([job['state'] for job in self.app.batch_jobs], ['失敗', '失敗'])
        self.assertFalse(self.app.busy)

    def test_preflight_dependency_failure_does_not_start_browser(self):
        with patch.object(m, 'find_ytdlp', side_effect=RuntimeError('missing downloader')):
            self.start()
        self.assertFalse(self.app.batch_active)
        self.app.start_browser_worker.assert_not_called()

    def test_destination_is_fixed_for_entire_batch(self):
        self.start()
        self.app.output_var.set(str(Path(self.temp.name) / 'other'))
        self.complete()
        self.assertTrue(all(item[1].parent == Path(self.temp.name) for item in self.downloads))


if __name__ == '__main__': unittest.main()
