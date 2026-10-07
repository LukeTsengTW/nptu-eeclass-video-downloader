#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eeClass Video Downloader (NPTU)

用途：
1. 貼上 eeClass 影片頁面網址，程式自動開啟專用 Firefox。
2. 首次由使用者在 Firefox 正常登入，再自動讀取影片頁面。
3. 優先解析 media Base64 JSON，列出並預選最高畫質。
4. 使用該 Firefox 視窗的即時 Cookie、Referer 與 yt-dlp 下載 MP4。

環境：Windows、Python 3.10+（含 Tk / venv / pip）、yt-dlp；Firefox 可自動下載。
首次自動建立隔離環境並安裝 Selenium 4.50.0；驅動程式由 Selenium Manager 取得。
僅下載使用者已取得觀看權限的普通 MP4，不處理 DRM。

"""

from __future__ import annotations

import base64
import configparser
from contextlib import contextmanager
from html.parser import HTMLParser
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
import html as html_lib
import json
import os
import queue
import re
import shutil
import sys
import importlib.util
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "NPTU eeClass 影片下載器 · 1.7"
EECLASS_HOST = "eeclass.nptu.edu.tw"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) "
    "Gecko/20100101 Firefox/143.0"
)


def domain_matches(request_host: str, cookie_host: str) -> bool:
    request_host = request_host.lower().strip(".")
    cookie_host = cookie_host.lower().strip(".")
    return request_host == cookie_host or request_host.endswith("." + cookie_host)


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(".")
    return name or "eeclass_video"


def firefox_profiles() -> list[Path]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    base = Path(appdata) / "Mozilla" / "Firefox"
    paths = []
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(base / "profiles.ini", encoding="utf-8-sig")
        for section in config.sections():
            if section.startswith("Profile") and config.has_option(section, "Path"):
                value = Path(config.get(section, "Path"))
                paths.append(base / value if config.get(section, "IsRelative", fallback="1") == "1" else value)
    except (OSError, configparser.Error):
        pass
    directory = base / "Profiles"
    if directory.exists():
        paths.extend(directory.iterdir())
    return list(dict.fromkeys(p.resolve() for p in paths if (p / "cookies.sqlite").is_file()))


def cookie_rows(profile):
    db = profile / "cookies.sqlite"
    query = "SELECT host, path, name, value, expiry, isSecure FROM moz_cookies"

    def read_rows(conn):
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        rows = conn.execute(query).fetchall()
        if version >= 16:
            rows = [(host, path, name, value, expiry / 1000 if expiry is not None else None, secure)
                    for host, path, name, value, expiry, secure in rows]
        return rows
    try:
        # A read-only SQLite connection includes committed WAL changes atomically.
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            return read_rows(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        # Some Windows installations deny sharing the live database.
        with tempfile.TemporaryDirectory(prefix="eeclass_cookie_") as td:
            target = Path(td) / "cookies.sqlite"
            shutil.copy2(db, target)
            for suffix in ("-wal", "-shm"):
                src = profile / ("cookies.sqlite" + suffix)
                if src.exists():
                    shutil.copy2(src, Path(td) / ("cookies.sqlite" + suffix))
            conn = sqlite3.connect(str(target), timeout=2)
            try:
                return read_rows(conn)
            finally:
                conn.close()


def firefox_cookie_candidates(url, log=None):
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    req_path = parsed.path or "/"
    now = int(time.time())
    candidates = []
    profiles = firefox_profiles()
    if log:
        log(f"[Firefox] 找到 {len(profiles)} 個可讀取 Cookie 的設定檔")
    for profile in profiles:
        try:
            rows = cookie_rows(profile)
        except (OSError, sqlite3.Error) as e:
            if log:
                log(f"[Firefox] {profile.name}：Cookie 資料庫讀取失敗（{type(e).__name__}）")
            continue
        jar = CookieJar(policy=DefaultCookiePolicy(
            strict_ns_domain=DefaultCookiePolicy.DomainStrictNonDomain))
        for c_host, c_path, name, value, expiry, is_secure in rows:
            # No leading dot means a host-only cookie.
            if not (domain_matches(host, c_host) if c_host.startswith(".") else host == c_host.lower()):
                continue
            path = c_path or "/"
            if expiry and int(expiry) < now:
                continue
            if is_secure and parsed.scheme != "https":
                continue
            jar.set_cookie(Cookie(
                version=0, name=name, value=value, port=None, port_specified=False,
                domain=c_host, domain_specified=c_host.startswith("."),
                domain_initial_dot=c_host.startswith("."), path=path, path_specified=True,
                secure=bool(is_secure), expires=int(expiry) if expiry else None,
                discard=not bool(expiry), comment=None, comment_url=None, rest={}))
        probe = urllib.request.Request(url)
        jar.add_cookie_header(probe)
        matching_header = probe.get_header("Cookie") or ""
        matching_count = len(matching_header.split("; ")) if matching_header else 0
        if log:
            log(f"[Firefox] {profile.name}：{matching_count} 個符合目標頁面的 Cookie（不顯示內容）")
        if matching_count:
            try:
                mtime = max(p.stat().st_mtime for p in (profile / "cookies.sqlite", profile / "cookies.sqlite-wal") if p.exists())
            except OSError:
                mtime = 0
            candidates.append((mtime, jar, profile))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [(jar, profile) for _, jar, profile in candidates]


def read_firefox_cookies_for(url: str) -> tuple[CookieJar, Path]:
    candidates = firefox_cookie_candidates(url)
    if not candidates:
        raise RuntimeError("沒有讀到符合該頁面的 Firefox Cookie。可能是登入狀態尚未寫入磁碟，或目前使用的設定檔未被找到。")
    return candidates[0]


def is_eeclass_https(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        return (parsed.scheme == "https" and parsed.hostname == EECLASS_HOST
                and parsed.port in (None, 443)
                and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


class EeclassRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, log=None):
        self.log = log

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Keep the Cookie header and any compatibility context on eeClass only.
        if not is_eeclass_https(newurl):
            raise urllib.error.URLError(
                "頁面要求離開 eeClass 或改用非 HTTPS 連線。請在 Firefox 完成登入後重試。"
            )
        if self.log:
            old_path = urllib.parse.urlparse(req.full_url).path
            new_path = urllib.parse.urlparse(newurl).path
            self.log(f"[重新導向] HTTP {code}：{old_path} → {new_path}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_eeclass_page(req, timeout=30, log=None, cookiejar=None):
    """Retry only missing-SKI strict-validation failures; retain TLS identity checks."""
    if not is_eeclass_https(req.full_url):
        raise urllib.error.URLError("只允許讀取 NPTU eeClass 的 HTTPS 頁面。")
    context = ssl.create_default_context()

    def attempt():
        handlers = [urllib.request.HTTPSHandler(context=context), EeclassRedirectHandler(log)]
        if cookiejar is not None:
            handlers.append(urllib.request.HTTPCookieProcessor(cookiejar))
        opener = urllib.request.build_opener(*handlers)
        return opener.open(req, timeout=timeout)

    try:
        return attempt()
    except urllib.error.URLError as error:
        reason = error.reason
        strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
        message = getattr(reason, "verify_message", "") or str(reason)
        if not (isinstance(reason, ssl.SSLCertVerificationError)
                and "missing subject key identifier" in message.lower()
                and strict and context.verify_flags & strict):
            raise
        # Python 3.13 enables strict RFC 5280 validation by default. Some older
        # certificate chains lack SKI. Relax strict formatting for this retry,
        # while CERT_REQUIRED, check_hostname, trust roots and TLS stay intact.
        context.verify_flags &= ~strict
        if log:
            log("[TLS 相容處理] 憑證鏈缺少 SKI，改用非嚴格 X.509 驗證重試一次；仍驗證信任鏈、有效期限與主機名稱。")
        return attempt()


def fetch_page(page_url: str, cookie_data=None, log=None, metadata=None) -> str:
    jar, _profile = cookie_data or read_firefox_cookies_for(page_url)
    req = urllib.request.Request(
        page_url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://eeclass.nptu.edu.tw/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.7,en;q=0.5",
        },
    )
    try:
        with open_eeclass_page(req, timeout=30, log=log, cookiejar=jar) as resp:
            raw = resp.read()
            if metadata is not None:
                metadata.update(url=resp.geturl(), status=resp.status,
                                content_type=resp.headers.get_content_type(), byte_count=len(raw))
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"eeClass 回傳 HTTP {e.code}。請確認 Firefox 仍登入 eeClass。") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"無法連線到 eeClass：{e.reason}") from e


class PlayerHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.media = []
        self.frames = []
        self.counts = {key: 0 for key in ("video", "source", "iframe", "password")}
        self.title = []
        self.in_title = False
        self.in_json = False
        self.json_scripts = []
        self.json_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.counts:
            self.counts[tag] += 1
        if tag in ("video", "source"):
            url = attrs.get("src") or attrs.get("data-src")
            if url:
                self.media.append({"src": url, "title": attrs.get("title"),
                                   "width": attrs.get("width"), "height": attrs.get("height")})
        if tag == "iframe" and attrs.get("src"):
            self.frames.append(attrs["src"])
        if tag == "input" and (attrs.get("type") or "").lower() == "password":
            self.counts["password"] += 1
        if tag == "title":
            self.in_title = True
        if tag == "script" and (attrs.get("type") or "").lower() == "application/json":
            self.in_json = True
            self.json_parts = []

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "script" and self.in_json:
            self.json_scripts.append("".join(self.json_parts))
            self.in_json = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if self.in_json:
            self.json_parts.append(data)


def parse_player_html(page_html):
    parser = PlayerHTMLParser()
    parser.feed(page_html)
    return parser


def extract_sources(page_html: str) -> list[dict]:
    """Read player data as data only; never execute page JavaScript."""
    found = []
    parser = parse_player_html(page_html)

    def integer(value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def collect(obj, inherited_title="eeclass_video", depth=0):
        if depth > 20:
            return
        if isinstance(obj, str):
            collect({"src": obj}, inherited_title, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                collect(item, inherited_title, depth + 1)
        elif isinstance(obj, dict):
            title = str(obj.get("title") or inherited_title)
            size = obj.get("size") if isinstance(obj.get("size"), dict) else {}
            for key in ("src", "file", "url"):
                value = obj.get(key)
                if not isinstance(value, str):
                    continue
                value = html_lib.unescape(value.replace("\\/", "/")).strip()
                try:
                    parsed = urllib.parse.urlsplit(value)
                except ValueError:
                    continue
                if parsed.scheme not in ("", "http", "https") or not parsed.path.lower().endswith(".mp4"):
                    continue
                found.append({"title": title, "url": value,
                              "width": integer(size.get("width") or obj.get("width")),
                              "height": integer(size.get("height") or obj.get("height"))})
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    collect(value, title, depth + 1)

    for match in re.finditer(r"\batob\s*\(\s*(['\"])(.*?)\1\s*\)", page_html, re.S):
        payload = re.sub(r"\s+", "", html_lib.unescape(match.group(2)))
        if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", payload):
            continue
        try:
            decoded = base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_").decode("utf-8-sig")
            obj = json.loads(decoded)
            if isinstance(obj, str):
                obj = json.loads(obj)
            collect(obj)
        except (ValueError, UnicodeError):
            continue

    for data in parser.json_scripts:
        try:
            collect(json.loads(data))
        except ValueError:
            pass
    # Also accept media = {...} when the site embeds JSON without Base64.
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\b(?:media|sources|playlist)\s*[:=]\s*(?=[\[{])", page_html):
        try:
            obj, _ = decoder.raw_decode(page_html[match.end():])
            collect(obj)
        except ValueError:
            pass
    collect(parser.media)
    unique = {}
    for item in found:
        previous = unique.get(item["url"])
        if previous is None or item["width"] * item["height"] > previous["width"] * previous["height"]:
            unique[item["url"]] = item
    return sorted(unique.values(), key=lambda item: (item["width"] * item["height"], item["width"], item["height"]), reverse=True)


def log_page_summary(page, metadata, log):
    parser = parse_player_html(page)
    title = re.sub(r"\s+", " ", "".join(parser.title)).strip()[:100] or "（無標題）"
    final_path = urllib.parse.urlparse(metadata.get("url", "")).path
    counts = parser.counts
    atob_count = len(re.findall(r"atob\s*\(", page))
    log(f"[頁面] HTTP {metadata.get('status', '?')}；{metadata.get('content_type', '?')}；{len(page)} 字元；路徑 {final_path}")
    log(f"[頁面] 標題：{title}")
    log(f"[播放器] video={counts['video']}；source={counts['source']}；iframe={counts['iframe']}；atob={atob_count}；MP4={page.lower().count('.mp4')}；密碼欄位={counts['password']}")
    return parser


def resolve_video_page(url, log):
    candidates = firefox_cookie_candidates(url, log=log)
    if not candidates:
        raise RuntimeError("程式沒有讀到該頁面的 Firefox Cookie。請按「複製紀錄」提供診斷；這不代表瀏覽器沒有登入。")
    failures = 0
    login_pages = 0
    seen_pages = 0
    for index, cookie_data in enumerate(candidates, 1):
        profile = cookie_data[1]
        log(f"[嘗試 {index}/{len(candidates)}] Firefox 設定檔：{profile.name}")
        try:
            metadata = {}
            page = fetch_page(url, cookie_data, log=log, metadata=metadata)
            seen_pages += 1
            parser = log_page_summary(page, metadata, log)
            sources = extract_sources(page)
            if sources:
                base = metadata.get("url") or url
                for source in sources:
                    source["url"] = urllib.parse.urljoin(base, source["url"])
                return sources, url, profile, display_title(sources, page, url)
            final_path = urllib.parse.urlparse(metadata.get("url") or url).path
            if final_path.startswith("/course/info/"):
                log("[判讀] 影片請求被導向課程資訊頁；不將課綱 iframe 當作影片。可使用「匯入網頁」讀取 Firefox 儲存的影片 HTML。")
                continue
            if parser.counts["password"]:
                login_pages += 1
                log("[判讀] 回應含密碼輸入欄位，可能是登入頁；繼續嘗試其他設定檔。")
                continue
            # Inspect only frames actually embedded by this page, on the same host.
            frames = list(dict.fromkeys(urllib.parse.urljoin(metadata.get("url") or url, frame) for frame in parser.frames))
            for frame in [value for value in frames if is_eeclass_https(value) and value != url][:3]:
                log("[內嵌頁面] " + urllib.parse.urlparse(frame).path)
                try:
                    # CookieJar selects paths and retains cookies set by previous responses.
                    frame_cookie = cookie_data
                    child_meta = {}
                    child = fetch_page(frame, frame_cookie, log=log, metadata=child_meta)
                    log_page_summary(child, child_meta, log)
                    sources = extract_sources(child)
                    if sources:
                        for source in sources:
                            source["url"] = urllib.parse.urljoin(child_meta.get("url") or frame, source["url"])
                        return sources, frame, profile, display_title(sources, child, url)
                except (RuntimeError, ValueError) as e:
                    log(f"[內嵌頁面未完成] {e}")
            log("[判讀] 已取得頁面，但沒有解析出 MP4；繼續嘗試其他設定檔。")
        except (RuntimeError, ValueError) as e:
            failures += 1
            log(f"[設定檔未完成] {e}")
    log(f"[診斷摘要] 設定檔={len(candidates)}；取得頁面={seen_pages}；疑似登入頁={login_pages}；連線或解析例外={failures}")
    raise RuntimeError("程式取得的頁面仍未解析出 MP4，尚無法確認是登入狀態未同步，或播放器資料由 JavaScript 載入。\n\n可按「匯入網頁」使用 Firefox 儲存的影片 HTML，或按「複製紀錄」提供診斷；紀錄不包含 Cookie 值或整份 HTML。")


def read_saved_video_page(filename, page_url):
    if not is_eeclass_https(page_url):
        raise ValueError("請先在網址欄填入這部影片的 eeClass 網址。")
    path = Path(filename)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("請選擇影片頁面的 HTML 檔案（上限 8 MB），不是影片檔。")
    page = path.read_text(encoding="utf-8-sig", errors="replace")
    sources = extract_sources(page)
    if not sources:
        raise ValueError("這份 HTML 沒有可辨識的 MP4。請在 Firefox 的影片頁按 Ctrl+U，再於原始碼分頁按 Ctrl+S 儲存後匯入。")
    for source in sources:
        source["url"] = urllib.parse.urljoin(page_url, source["url"])
    return sources, display_title(sources, page, page_url)


def quality_label(item: dict) -> str:
    if item.get("width") and item.get("height"):
        return f"{item['width']} × {item['height']}"
    return "未知解析度"


def get_downloads_dir() -> Path:
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
                return Path(os.path.expandvars(value))
        except OSError:
            pass
    return Path.home() / "Downloads"


def find_ytdlp() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--ytdlp-worker"]
    executable = shutil.which("yt-dlp")
    if executable:
        return [executable]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise RuntimeError("找不到 yt-dlp。請確認原先安裝的 yt-dlp 仍可使用，或安裝至目前的 Python 環境。")


def build_download_command(executable, src, page_url, profile, output_path, cookie_file=None, user_agent=None):
    auth = ["--cookies", str(cookie_file)] if cookie_file else ["--cookies-from-browser", f"firefox:{profile}"]
    if user_agent:
        auth += ["--user-agent", user_agent]
    return [*executable, "--ignore-config", "--newline", "--no-color", "--progress",
            "--encoding", "utf-8", "--no-overwrites", "--no-playlist",
            "--progress-template",
            "download:EECLASS_PROGRESS|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s",
            *auth, "--referer", page_url,
            "-o", str(output_path).replace("%", "%%"), src["url"]]


def parse_progress(line):
    if not line.startswith("EECLASS_PROGRESS|"):
        return None
    values = line.strip().split("|")[1:]
    if len(values) != 5:
        return None
    def number(value):
        try:
            result = float(value)
            return result if 0 <= result < float("inf") else None
        except ValueError:
            return None
    downloaded, total, estimate, speed, eta = map(number, values)
    size = total or estimate
    percent = min(100, downloaded / size * 100) if size and downloaded is not None else None
    detail = []
    if downloaded is not None:
        detail.append(f"{downloaded / 1048576:.1f} MB" + (f" / {size / 1048576:.1f} MB" if size else ""))
    if speed is not None:
        detail.append(f"{speed / 1048576:.1f} MB/s")
    if eta is not None:
        detail.append(f"剩餘 {int(eta) // 60:02d}:{int(eta) % 60:02d}")
    return percent, "  ·  ".join(detail)


def display_title(sources, page, url):
    title = sources[0].get("title", "").strip()
    if title and title != "eeclass_video":
        return title
    match = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    if match:
        title = html_lib.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
        if title:
            return title
    return "eeclass_" + (urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1] or "video")


def collect_video_urls(values):
    """Keep row numbers for validation; only the first row is required."""
    if not values or not values[0].strip():
        raise ValueError("第一個網址欄位為必填，請先輸入影片網址。")
    urls = []
    for row, value in enumerate(values, 1):
        url = value.strip()
        if not url:
            continue
        if not is_eeclass_https(url):
            raise ValueError(f"第 {row} 個網址格式不正確，請輸入 https://eeclass.nptu.edu.tw/ 的影片頁面網址。")
        urls.append(url)
    return urls


def download_output_path(out_dir, title, source):
    title = title.strip()
    if title.lower().endswith(".mp4"):
        title = title[:-4]
    title = sanitize_filename(title)[:150].rstrip(" .") or "eeclass_video"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", title, re.I):
        title = "_" + title
    quality = f"_{source['width']}x{source['height']}" if source.get("width") and source.get("height") else ""
    output_path = out_dir / f"{title}{quality}.mp4"
    counter = 2
    while any(Path(str(output_path) + suffix).exists() for suffix in ("", ".part", ".ytdl")):
        output_path = out_dir / f"{title}{quality} ({counter}).mp4"
        counter += 1
    return output_path


SELENIUM_VERSION = "4.50.0"


def automation_directory():
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".local" / "share"))
    return base / "NPTUeeClassDownloader"


def quiet_process_kwargs():
    return {"creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0}


@contextmanager
def setup_lock(report, timeout=600):
    """OS lock is released even if setup crashes; the file itself is reusable."""
    root = automation_directory()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "setup.lock").open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        waiting = False
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("另一個下載器仍在設定瀏覽器，請等候完成後再試。")
                if not waiting:
                    report("另一個下載器正在設定，等候完成後共用元件…")
                    waiting = True
                time.sleep(0.25)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def automation_python(report):
    if getattr(sys, "frozen", False):
        # Selenium is bundled; never run this executable with Python's -m venv/pip.
        import selenium
        if selenium.__version__ != SELENIUM_VERSION:
            raise RuntimeError("內建瀏覽器元件版本不正確，請重新下載完整的 EXE。")
        return Path(sys.executable)
    with setup_lock(report):
        return _automation_python(report)


def browser_worker_command(report):
    python = automation_python(report)
    if getattr(sys, "frozen", False):
        return [str(python), "--browser-worker"]
    return [str(python), "-u", str(Path(__file__).resolve()), "--browser-worker"]


def _automation_python(report):
    """Install into a private venv, leaving the user's Python packages untouched."""
    root = automation_directory()
    runtime = root / f"runtime-{sys.version_info.major}.{sys.version_info.minor}"
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    check = [str(python), "-c", f"import selenium; assert selenium.__version__ == '{SELENIUM_VERSION}'"]
    if python.is_file():
        result = subprocess.run(check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=20, **quiet_process_kwargs())
        if result.returncode == 0:
            return python
    root.mkdir(parents=True, exist_ok=True)
    if not python.is_file():
        report("首次設定：建立瀏覽器自動化環境…")
        result = subprocess.run([sys.executable, "-m", "venv", str(runtime)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=120, **quiet_process_kwargs())
        if result.returncode:
            raise RuntimeError("無法建立自動化環境。請確認 Python 已包含 pip 與 venv，並可寫入使用者的 AppData 資料夾。")
    report("首次設定：下載並安裝瀏覽器元件，請稍候…")
    result = subprocess.run([str(python), "-m", "pip", "--isolated", "install",
        "--disable-pip-version-check", "--no-input", "--index-url", "https://pypi.org/simple",
        f"selenium=={SELENIUM_VERSION}"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=300, **quiet_process_kwargs())
    if result.returncode:
        raise RuntimeError(f"自動化元件安裝失敗（代碼 {result.returncode}）。請確認網路可連線至 PyPI，之後再按解析。手動匯入功能仍可使用。")
    result = subprocess.run(check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=20, **quiet_process_kwargs())
    if result.returncode:
        raise RuntimeError("自動化元件驗證失敗，請重新開啟程式後再試。")
    return python


def firefox_executable():
    candidates = [shutil.which("firefox")]
    if os.name == "nt":
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            value = os.environ.get(key)
            if value:
                candidates.append(str(Path(value) / "Mozilla Firefox" / "firefox.exe"))
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe") as key:
                        candidates.append(winreg.QueryValue(key, None))
                except OSError:
                    pass
        except ImportError:
            pass
    elif sys.platform == "darwin":
        candidates.append("/Applications/Firefox.app/Contents/MacOS/firefox")
    return next((value for value in candidates if value and Path(value).is_file()), None)


def selenium_cache_directory():
    # Reuse Selenium's existing assets, including drivers downloaded by v1.4.
    return Path(os.environ.get("SE_CACHE_PATH") or (Path.home() / ".cache" / "selenium")).expanduser().resolve()


def executable_version(path, product):
    if not path or not Path(path).is_file():
        return None
    try:
        result = subprocess.run([str(path), "--version"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15,
            **quiet_process_kwargs())
        text = result.stdout.decode("utf-8", errors="replace")
        match = re.search(re.escape(product) + r"\s+(\d+(?:\.\d+)+)", text, re.I)
        return match.group(1) if result.returncode == 0 and match else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def cached_firefoxes(cache):
    suffix = "firefox.exe" if os.name == "nt" else (
        "Firefox.app/Contents/MacOS/firefox" if sys.platform == "darwin" else "firefox")
    # Bounded search of the known Selenium layout; never scan the user's drive.
    paths = list((cache / "firefox").glob("*/*/" + suffix))
    def version_key(path):
        relative = path.relative_to(cache / "firefox")
        return tuple(int(n) for n in re.findall(r"\d+", relative.parts[1]))
    return sorted(paths, key=version_key, reverse=True)


def asset_signature(path):
    info = Path(path).stat()
    return [info.st_size, info.st_mtime_ns]


def prepare_firefox(report):
    """Reuse installed/cached Firefox before allowing a browser download."""
    from selenium.webdriver.common.selenium_manager import SeleniumManager
    with setup_lock(report):
        shared_cache = selenium_cache_directory()
        owned_cache = automation_directory() / "browser-cache"
        state_file = automation_directory() / "browser-assets.json"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except (OSError, ValueError):
            state = {}
        installed = firefox_executable()
        candidates = [installed, state.get("browser_path"),
                      *cached_firefoxes(owned_cache), *cached_firefoxes(shared_cache)]
        browser = version = None
        for candidate in candidates:
            if isinstance(candidate, (str, Path)):
                version = executable_version(candidate, "Firefox")
                if version:
                    browser = str(Path(candidate).resolve())
                    break
        if browser:
            report("使用電腦已安裝的 Firefox…" if installed and Path(installed).resolve() == Path(browser)
                   else "重用已下載的專用 Firefox，不重新下載…")
            driver = state.get("driver_path")
            try:
                if (state.get("browser_path") == browser and
                    state.get("browser_signature") == asset_signature(browser) and
                    isinstance(driver, str) and
                    state.get("driver_signature") == asset_signature(driver) and
                    executable_version(driver, "geckodriver")):
                    return browser, driver
            except OSError:
                pass
        else:
            report("首次設定：找不到可用 Firefox，正在下載專用瀏覽器（之後會重用）…")
            # Repair incomplete extractions only inside this application's cache.
            # Never delete system installations or another application's shared cache.
            for candidate in cached_firefoxes(owned_cache):
                relative = candidate.relative_to(owned_cache / "firefox")
                version_dir = owned_cache / "firefox" / relative.parts[0] / relative.parts[1]
                if version_dir.resolve().is_relative_to(owned_cache.resolve()) and not version_dir.is_symlink():
                    shutil.rmtree(version_dir)
        cache = owned_cache
        args = ["--browser", "firefox", "--cache-path", str(cache), "--avoid-stats", "--timeout", "300"]
        if browser:
            args += ["--browser-path", browser, "--browser-version", version, "--avoid-browser-download"]
        manager = SeleniumManager()
        result = None
        if browser:
            # Check both caches for a usable driver before permitting a download.
            for existing_cache in dict.fromkeys([owned_cache, shared_cache]):
                offline_args = list(args)
                offline_args[offline_args.index("--cache-path") + 1] = str(existing_cache)
                try:
                    candidate_result = manager.binary_paths([*offline_args, "--offline"])
                    if executable_version(candidate_result.get("driver_path"), "geckodriver"):
                        result = candidate_result
                        break
                except Exception:
                    continue
        if result is None:
            try:
                result = manager.binary_paths(args)
            except Exception as error:
                raise RuntimeError("瀏覽器元件準備失敗。請確認網路與磁碟空間後重新解析；已完成的下載會重用。"
                                   f"（{type(error).__name__}）") from error
        browser = browser or result.get("browser_path")
        driver = result.get("driver_path")
        if not executable_version(browser, "Firefox") or not executable_version(driver, "geckodriver"):
            raise RuntimeError("瀏覽器或驅動程式驗證失敗，尚未記錄為設定完成。請重試，或安裝 Firefox 後再解析。")
        browser, driver = str(Path(browser).resolve()), str(Path(driver).resolve())
        state = {"browser_path": browser, "driver_path": driver,
                 "browser_signature": asset_signature(browser), "driver_signature": asset_signature(driver)}
        temporary = state_file.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            temporary.replace(state_file)
        finally:
            temporary.unlink(missing_ok=True)
        return browser, driver


def media_snapshot(driver, requested_url):
    """Read media only when the controlled browser is on the requested document."""
    current_url = driver.current_url
    if not is_eeclass_https(current_url):
        return None
    if urllib.parse.urlparse(current_url).path.rstrip("/") != urllib.parse.urlparse(requested_url).path.rstrip("/"):
        return None
    page = driver.page_source
    sources = extract_sources(page)
    if not sources:
        data = driver.execute_script("""return Array.from(document.querySelectorAll('video')).map(v => ({
            url: v.currentSrc || v.src, width: v.videoWidth || 0, height: v.videoHeight || 0,
            title: document.title || 'eeclass_video'}));""") or []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                sources.extend(extract_sources('media = ' + json.dumps({"src": [{
                    "src": item["url"], "width": item.get("width"), "height": item.get("height"),
                    "title": item.get("title")}]})))
    valid = []
    for source in sources:
        source = dict(source)
        source["url"] = urllib.parse.urljoin(current_url, source["url"])
        if is_eeclass_https(source["url"]):
            valid.append(source)
    if not valid:
        return None
    valid.sort(key=lambda item: (item["width"] * item["height"], item["width"], item["height"]), reverse=True)
    # This WebDriver call reads cookies for the current eeClass document only.
    cookies = [cookie for cookie in driver.get_cookies()
               if domain_matches(EECLASS_HOST, cookie.get("domain", "")) and cookie.get("domain")]
    return {"sources": valid, "page_url": current_url, "title": display_title(valid, page, current_url),
            "cookies": cookies, "user_agent": driver.execute_script("return navigator.userAgent;")}


def browser_cookie_jar(cookies):
    from http.cookiejar import MozillaCookieJar
    jar = MozillaCookieJar()
    for item in cookies:
        domain = str(item.get("domain") or "")
        if not domain or not domain_matches(EECLASS_HOST, domain):
            continue
        name, value = str(item.get("name") or ""), str(item.get("value") or "")
        path = str(item.get("path") or "/")
        if not name or any(char in name + value + domain + path for char in "\r\n\t"):
            continue
        expiry = item.get("expiry")
        try:
            expiry = int(expiry) if expiry is not None else None
        except (TypeError, ValueError):
            continue
        if expiry is not None and expiry <= time.time():
            continue
        jar.set_cookie(Cookie(0, name, value, None, False, domain, domain.startswith("."),
            domain.startswith("."), path, True, bool(item.get("secure")), expiry,
            expiry is None, None, None, {"HttpOnly": None} if item.get("httpOnly") else {}))
    return jar


def resolve_in_browser(driver, url, commands, emit, timeout=300):
    """Wait for the user's normal login; never enter passwords or automate consent."""
    if not is_eeclass_https(url):
        raise ValueError("請輸入 NPTU eeClass 的 HTTPS 影片網址。")
    from selenium.common.exceptions import TimeoutException
    try:
        driver.get(url)
    except TimeoutException:
        pass
    deadline = time.monotonic() + timeout
    prompted = False
    while time.monotonic() < deadline:
        try:
            command = commands.get_nowait()
        except queue.Empty:
            command = None
        if command:
            if command.get("op") in ("cancel", "quit"):
                return {"type": "cancelled", "quit": command.get("op") == "quit"}
            if command.get("op") == "continue":
                # A login flow may open another tab. Navigate the original controlled tab.
                if driver.window_handles:
                    driver.switch_to.window(driver.window_handles[0])
                try:
                    driver.get(url)
                except TimeoutException:
                    pass
                prompted = False
        result = media_snapshot(driver, url)
        if result:
            return {"type": "result", **result}
        if not prompted:
            emit({"type": "login_required", "text": "請在程式開啟的 Firefox 完成登入；完成後按「登入完成，繼續」。若影片頁已開啟，會自動解析。"})
            prompted = True
        time.sleep(0.75)
    raise RuntimeError("等候影片超過 5 分鐘。請確認專用 Firefox 視窗可以顯示該影片，再重新解析。")


def browser_worker_main():
    """Private JSON pipe between this process and the GUI; never print secrets as logs."""
    import socket
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    commands = queue.Queue()
    request_id = None

    def emit(value):
        value = {**value, "request_id": request_id}
        sys.stdout.write(json.dumps(value, ensure_ascii=True) + "\n")
        sys.stdout.flush()

    def read_commands():
        for line in sys.stdin:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    commands.put(value)
            except ValueError:
                pass
        commands.put({"op": "quit"})

    threading.Thread(target=read_commands, daemon=True).start()
    driver = None
    try:
        while True:
            command = commands.get()
            if command.get("op") == "quit":
                break
            if command.get("op") != "resolve":
                continue
            request_id = command.get("request_id")
            try:
                if driver is not None:
                    try:
                        if not driver.window_handles:
                            raise RuntimeError("closed")
                    except Exception:
                        try:
                            driver.quit()
                        except Exception:
                            pass
                        driver = None
                if driver is None:
                    executable, driver_path = prepare_firefox(
                        lambda text: emit({"type": "status", "text": text}))
                    emit({"type": "status", "text": "瀏覽器元件已就緒，正在開啟專用 Firefox…"})
                    profile = automation_directory() / "firefox-profile"
                    profile.mkdir(parents=True, exist_ok=True)
                    options = Options()
                    options.binary_location = executable
                    options.accept_insecure_certs = False
                    options.page_load_strategy = "eager"
                    options.add_argument("-no-remote")
                    options.add_argument("-profile")
                    options.add_argument(str(profile))
                    options.set_preference("signon.rememberSignons", False)
                    # Explicit ephemeral Marionette port avoids geckodriver's in-place-profile bug.
                    with socket.socket() as sock:
                        sock.bind(("127.0.0.1", 0))
                        port = sock.getsockname()[1]
                    service = Service(executable_path=driver_path, service_args=["--marionette-port", str(port)],
                        log_output=subprocess.DEVNULL,
                        popen_kw={"creation_flags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0})
                    driver = webdriver.Firefox(options=options, service=service)
                    driver.set_page_load_timeout(30)
                    driver.set_script_timeout(10)
                result = resolve_in_browser(driver, command["url"], commands, emit)
                emit(result)
                if result.get("quit"):
                    break
            except Exception as error:
                if isinstance(error, (RuntimeError, ValueError)):
                    message = str(error)
                else:
                    message = f"Firefox 自動操作未完成（{type(error).__name__}）。請確認 Firefox 視窗仍開啟、驅動程式下載網路正常，再重試。"
                emit({"type": "error", "text": message})
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass



class EeclassDownloaderApp(tk.Tk):
    BG = "#F3F5FA"
    INK = "#1C2943"
    MUTED = "#71809A"
    ACCENT = "#4263EB"

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("800x840")
        self.minsize(740, 840)
        self.configure(bg=self.BG)
        self.sources = []
        self.page_url = ""
        self.profile = None
        self.busy = False
        self.msg_queue = queue.Queue()
        self.browser_auth = None
        self.browser_process = None
        self.browser_write_lock = threading.Lock()
        self.browser_resolving = False
        self.closing = False
        self.operation_id = 0
        self.batch_active = False
        self.batch_jobs = []
        self.batch_index = -1
        self.batch_stop_requested = False
        self.batch_phase = "idle"
        self.url_var = tk.StringVar()
        self.url_vars = [self.url_var]
        self.url_rows = []
        self.quality_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(get_downloads_dir()))
        self.status_var = tk.StringVar(value="準備就緒")
        self.detail_var = tk.StringVar(value="貼上影片網址即可；首次會自動設定，並開啟 Firefox 讓你登入。")
        self.percent_var = tk.StringVar(value="")
        self.video_var = tk.StringVar(value="尚未選擇影片")
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self._drain_queue)

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        font = ("Microsoft JhengHei UI", 10)
        self.option_add("*Font", font)
        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background="white")
        style.configure("TLabel", background="white", foreground=self.INK, font=font)
        style.configure("Muted.TLabel", foreground=self.MUTED)
        style.configure("Title.TLabel", font=("Microsoft JhengHei UI", 23, "bold"), background=self.BG)
        style.configure("Sub.TLabel", foreground=self.MUTED, background=self.BG)
        style.configure("Heading.TLabel", font=("Microsoft JhengHei UI", 11, "bold"))
        style.configure("TEntry", padding=9, fieldbackground="#FAFBFE", bordercolor="#DEE4F0", lightcolor="#DEE4F0", darkcolor="#DEE4F0")
        style.configure("TCombobox", padding=8, fieldbackground="#FAFBFE", arrowsize=15)
        style.map("TCombobox", fieldbackground=[("readonly", "#FAFBFE")], selectbackground=[("readonly", "#FAFBFE")], selectforeground=[("readonly", self.INK)])
        style.configure("TButton", padding=(16, 10), background="#EDF1FA", foreground=self.INK, borderwidth=0)
        style.map("TButton", background=[("active", "#E0E7F5"), ("disabled", "#F1F3F7")], foreground=[("disabled", "#A3ABBB")])
        style.configure("Primary.TButton", background=self.ACCENT, foreground="white", font=("Microsoft JhengHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("disabled", "#CBD4F2"), ("active", "#3451CE")], foreground=[("disabled", "#FFFFFF")])
        style.configure("Horizontal.TProgressbar", background=self.ACCENT, troughcolor="#E9EDF7", borderwidth=0, lightcolor=self.ACCENT, darkcolor=self.ACCENT)
        # Scroll the whole form so added URL rows never push controls off-screen.
        shell = ttk.Frame(self)
        shell.pack(fill="both", expand=True)
        self.form_canvas = tk.Canvas(shell, bg=self.BG, highlightthickness=0)
        form_scroll = ttk.Scrollbar(shell, orient="vertical", command=self.form_canvas.yview)
        form_scroll.pack(side="right", fill="y")
        self.form_canvas.pack(side="left", fill="both", expand=True)
        self.form_canvas.configure(yscrollcommand=form_scroll.set)
        root = ttk.Frame(self.form_canvas, padding=28)
        form_window = self.form_canvas.create_window((0, 0), window=root, anchor="nw")
        root.bind("<Configure>", lambda _: self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all")))
        self.form_canvas.bind("<Configure>", lambda event: self.form_canvas.itemconfigure(form_window, width=event.width))
        self.bind("<MouseWheel>", lambda event: self.form_canvas.yview_scroll(-int(event.delta / 120), "units")
                  if event.widget is not self.log else None)
        root.columnconfigure(0, weight=1)
        ttk.Label(root, text="eeClass 影片下載", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(root, text="NPTU  /  將課程影片儲存為 MP4", style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 22))

        link = ttk.Frame(root, style="Card.TFrame", padding=18)
        link.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        link.columnconfigure(0, weight=1)
        ttk.Label(link, text="01   貼上影片連結", style="Heading.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.add_url_btn = ttk.Button(link, text="＋ 新增網址", command=self.add_url_row)
        self.add_url_btn.grid(row=0, column=1, sticky="e", pady=(0, 10))
        self.urls_frame = ttk.Frame(link, style="Card.TFrame")
        self.urls_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.urls_frame.columnconfigure(0, weight=1)
        self.add_url_row(first=True)
        self.resolve_btn = ttk.Button(link, text="解析影片", style="Primary.TButton", command=self.resolve)
        self.resolve_btn.grid(row=3, column=1, sticky="e", pady=(10, 0))
        self.import_btn = ttk.Button(root, text="匯入 HTML（備用）", command=self.import_page)
        self.import_btn.grid(row=7, column=0, sticky="w", pady=(8, 0))
        ttk.Label(link, text="第一欄必填，其餘空白會略過。多筆網址依序下載最高畫質，\n檔名自動使用影片標題；開始前請先選好下方儲存位置。", style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(9, 0))

        card = ttk.Frame(root, style="Card.TFrame", padding=18)
        card.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text="02   確認下載內容", style="Heading.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        self.video_label = ttk.Label(card, textvariable=self.video_var, style="Muted.TLabel", wraplength=650)
        self.video_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(7, 14))
        ttk.Label(card, text="影片檔名").grid(row=2, column=0, sticky="w", pady=(0, 6))
        ttk.Label(card, text="畫質 · 預設最高").grid(row=2, column=1, sticky="w", padx=(16, 0), pady=(0, 6))
        self.name_entry = ttk.Entry(card, textvariable=self.name_var)
        self.name_entry.grid(row=3, column=0, sticky="ew")
        self.quality_box = ttk.Combobox(card, textvariable=self.quality_var, state="disabled", width=18)
        self.quality_box.grid(row=3, column=1, sticky="ew", padx=(16, 10))
        self.copy_btn = ttk.Button(card, text="複製連結", command=self.copy_mp4, state="disabled")
        self.copy_btn.grid(row=3, column=2)
        ttk.Label(card, text="儲存位置").grid(row=4, column=0, sticky="w", pady=(16, 6))
        self.output_entry = ttk.Entry(card, textvariable=self.output_var)
        self.output_entry.grid(row=5, column=0, columnspan=2, sticky="ew", padx=(0, 10))
        self.folder_btn = ttk.Button(card, text="選擇資料夾", command=self.choose_folder)
        self.folder_btn.grid(row=5, column=2)

        progress_card = ttk.Frame(root, style="Card.TFrame", padding=18)
        progress_card.grid(row=4, column=0, sticky="ew")
        progress_card.columnconfigure(0, weight=1)
        ttk.Label(progress_card, textvariable=self.status_var, style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(progress_card, textvariable=self.percent_var).grid(row=0, column=1, sticky="e")
        self.progress = ttk.Progressbar(progress_card, mode="determinate", maximum=100)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 10))
        ttk.Label(progress_card, textvariable=self.detail_var, style="Muted.TLabel", wraplength=650).grid(row=2, column=0, columnspan=2, sticky="w")
        self.browser_actions = ttk.Frame(progress_card, style="Card.TFrame")
        self.browser_actions.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.browser_actions.grid_remove()
        self.batch_stop_btn = ttk.Button(progress_card, text="完成此支後停止", command=self.stop_batch)
        self.batch_stop_btn.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.batch_stop_btn.grid_remove()
        self.batch_frame = ttk.Frame(root)
        self.batch_frame.grid(row=8, column=0, sticky="ew", pady=(14, 0))
        self.batch_frame.columnconfigure(0, weight=1)
        self.batch_table = ttk.Treeview(self.batch_frame, columns=("number", "video", "state"), show="headings", height=5)
        for column, label, width in (("number", "序號", 45), ("video", "影片／網址", 450), ("state", "狀態", 110)):
            self.batch_table.heading(column, text=label)
            self.batch_table.column(column, width=width, stretch=column == "video")
        self.batch_table.grid(row=0, column=0, sticky="ew")
        batch_scroll = ttk.Scrollbar(self.batch_frame, command=self.batch_table.yview)
        batch_scroll.grid(row=0, column=1, sticky="ns")
        self.batch_table.configure(yscrollcommand=batch_scroll.set)
        self.batch_frame.grid_remove()
        actions = ttk.Frame(root)
        actions.grid(row=5, column=0, sticky="ew", pady=(18, 0))
        self.download_btn = ttk.Button(actions, text="下載 MP4", style="Primary.TButton", command=self.download, state="disabled")
        self.download_btn.pack(side="right")
        ttk.Button(actions, text="開啟資料夾", command=self.open_folder).pack(side="right", padx=10)
        self.log_btn = ttk.Button(actions, text="顯示紀錄", command=self.toggle_log)
        self.log_btn.pack(side="left")
        self.continue_btn = ttk.Button(self.browser_actions, text="登入完成，繼續", command=lambda: self.send_browser_command({"op": "continue"}))
        self.cancel_btn = ttk.Button(self.browser_actions, text="取消解析", command=lambda: self.send_browser_command({"op": "cancel"}))
        ttk.Button(actions, text="複製紀錄", command=self.copy_log).pack(side="left", padx=(8, 0))
        self.log_frame = ttk.Frame(root)
        self.log_frame.grid(row=6, column=0, sticky="nsew", pady=(14, 0))
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(self.log_frame, height=7, wrap="word", bg="#EAF0F8", fg=self.INK,
                           relief="flat", padx=12, pady=10, state="disabled", font=("Microsoft JhengHei UI", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.log_frame, command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log_frame.grid_remove()
        root.rowconfigure(6, weight=1)
        self.url_entry.focus_set()

    def add_url_row(self, first=False):
        if self.busy or self.batch_active:
            return
        variable = self.url_var if first else tk.StringVar()
        if not first:
            self.url_vars.append(variable)
        row = ttk.Frame(self.urls_frame, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 7))
        label = ttk.Label(row, text="1 *" if first else str(len(self.url_vars)), width=4)
        label.pack(side="left")
        entry = ttk.Entry(row, textvariable=variable)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _: self.resolve())
        remove = None
        if first:
            self.url_entry = entry
        else:
            remove = ttk.Button(row, text="−", width=3, command=lambda: self.remove_url_row(variable))
            remove.pack(side="right", padx=(8, 0))
        trace = variable.trace_add("write", self._url_changed)
        self.url_rows.append((variable, row, label, entry, remove, trace))
        if not first:
            entry.focus_set()

    def remove_url_row(self, variable):
        if self.busy or self.batch_active or variable is self.url_var:
            return
        for item in self.url_rows:
            if item[0] is variable:
                variable.trace_remove("write", item[5])
                item[1].destroy()
                self.url_rows.remove(item)
                self.url_vars.remove(variable)
                break
        for index, item in enumerate(self.url_rows, 1):
            item[2].configure(text="1 *" if index == 1 else str(index))
        self._url_changed()

    def _write_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", str(text).rstrip() + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 600:
            self.log.delete("1.0", "100.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log.get("1.0", "end-1c"))
        self.status_var.set("已複製診斷紀錄")

    def toggle_log(self):
        if self.log_frame.winfo_ismapped():
            self.log_frame.grid_remove()
            self.log_btn.configure(text="顯示紀錄")
            self.geometry("800x840")
        else:
            self.log_frame.grid()
            self.log_btn.configure(text="隱藏紀錄")
            self.geometry("800x990")

    def _set_busy(self, value):
        self.busy = value
        if not value:
            self.browser_resolving = False
            self.continue_btn.pack_forget()
            self.cancel_btn.pack_forget()
            self.browser_actions.grid_remove()
        locked = value or self.batch_active
        state = "disabled" if locked else "normal"
        for widget in (self.add_url_btn, self.resolve_btn, self.import_btn, self.name_entry, self.output_entry, self.folder_btn):
            widget.configure(state=state)
        if sum(bool(variable.get().strip()) for variable in self.url_vars) > 1:
            self.import_btn.configure(state="disabled")
        for _, _, _, entry, remove, _ in self.url_rows:
            entry.configure(state=state)
            if remove:
                remove.configure(state=state)
        ready = bool(self.sources) and not locked
        self.download_btn.configure(state="normal" if ready else "disabled")
        self.copy_btn.configure(state="normal" if ready else "disabled")
        self.quality_box.configure(state="readonly" if ready else "disabled")

    def _url_changed(self, *_):
        if self.busy or self.batch_active:
            return
        self.sources = []
        self.page_url = ""
        self.profile = None
        self.browser_auth = None
        self.quality_box.set("")
        self.quality_box["values"] = []
        self.name_var.set("")
        self.video_var.set("尚未選擇影片")
        self.progress["value"] = 0
        self.percent_var.set("")
        self.status_var.set("等待解析")
        self.detail_var.set("貼上網址後，按「解析影片」。")
        count = sum(bool(variable.get().strip()) for variable in self.url_vars)
        self.resolve_btn.configure(text=f"批次下載（{count}）" if count > 1 else "解析影片")
        if count > 1:
            self.detail_var.set(f"共 {count} 支影片，將以最高畫質依序下載。請先確認儲存位置。")
        self._set_busy(False)

    def _batch_status(self, state, detail=None):
        job = self.batch_jobs[self.batch_index]
        job["state"] = state
        if detail is not None:
            job["detail"] = str(detail)
        self.batch_table.item(str(self.batch_index), values=(self.batch_index + 1, job.get("title") or job["url"], state))
        self.batch_table.see(str(self.batch_index))

    def start_batch(self, urls):
        # Validate dependencies and destination before downloading any item.
        try:
            find_ytdlp()
            destination = Path(self.output_var.get().strip() or get_downloads_dir()).expanduser().resolve()
            destination.mkdir(parents=True, exist_ok=True)
        except (OSError, RuntimeError) as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
            return
        self.batch_output_dir = destination
        self.batch_jobs = [{"url": url, "state": "待處理"} for url in urls]
        self.batch_index = -1
        self.batch_active = True
        self.batch_stop_requested = False
        self.batch_phase = "idle"
        self.batch_table.delete(*self.batch_table.get_children())
        for index, job in enumerate(self.batch_jobs):
            self.batch_table.insert("", "end", iid=str(index), values=(index + 1, job["url"], "待處理"))
        self.batch_frame.grid()
        self.batch_stop_btn.configure(state="normal", text="完成此支後停止")
        self.batch_stop_btn.grid()
        self._write_log(f"[批次開始] {len(urls)} 支影片；儲存位置：{destination}")
        self._next_batch_item()

    def stop_batch(self):
        if not self.batch_active:
            return
        self.batch_stop_requested = True
        self.batch_stop_btn.configure(state="disabled", text="完成此支後停止…")
        self._write_log("[批次] 已要求停止；目前影片完成後，不再開始下一支。")

    def _next_batch_item(self):
        if not self.batch_active:
            return
        if self.batch_stop_requested or self.batch_index + 1 >= len(self.batch_jobs):
            for index in range(self.batch_index + 1, len(self.batch_jobs)):
                job = self.batch_jobs[index]
                job["state"] = "未下載"
                self.batch_table.item(str(index), values=(index + 1, job["url"], "未下載"))
            succeeded = sum(job["state"] == "完成" for job in self.batch_jobs)
            failed = sum(job["state"] == "失敗" for job in self.batch_jobs)
            remaining = len(self.batch_jobs) - succeeded - failed
            self.batch_active = False
            self.batch_phase = "idle"
            self.batch_stop_btn.grid_remove()
            self._set_busy(False)
            self.status_var.set("批次已停止" if self.batch_stop_requested else "批次處理結束")
            summary = f"成功 {succeeded} 支／失敗 {failed} 支／未完成 {remaining} 支。個別結果請見下方清單與紀錄。"
            self.detail_var.set(summary)
            self._write_log("[批次結果] " + summary)
            return
        self.batch_index += 1
        self.batch_phase = "resolve"
        self._batch_status("解析中")
        self._resolve_url(self.batch_jobs[self.batch_index]["url"])

    def _finish_batch_item(self, state, detail):
        if not self.batch_active or self.batch_phase == "idle":
            return
        self._batch_status(state, detail)
        self._write_log(f"[批次 {self.batch_index + 1}/{len(self.batch_jobs)} {state}] {detail}")
        self.batch_phase = "idle"
        self.operation_id += 1  # Reject late events from the completed operation.
        self.browser_resolving = False
        self._set_busy(True)
        self.after(100, self._next_batch_item)

    def _drain_queue(self):
        try:
            for _ in range(100):
                event = self.msg_queue.get_nowait()
                kind, payload = event[:2]
                if len(event) > 2 and event[2] != self.operation_id:
                    continue
                if kind == "log":
                    self._write_log(payload)
                elif kind == "browser_status":
                    self.status_var.set(f"正在準備 {self.batch_index + 1}/{len(self.batch_jobs)}" if self.batch_active else "正在準備自動解析")
                    self.detail_var.set(str(payload))
                    self._write_log(str(payload))
                elif kind == "browser_ready":
                    self.browser_actions.grid()
                    self.cancel_btn.pack(side="left", padx=(8, 0))
                elif kind == "browser_login":
                    self.status_var.set("等待 Firefox 登入或影片載入")
                    self.detail_var.set(str(payload))
                    self.browser_actions.grid()
                    self.continue_btn.pack(side="left", padx=(8, 0))
                elif kind == "browser_cancelled":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self.status_var.set("已取消解析")
                    self.detail_var.set("Firefox 視窗已保留，可完成登入後重新解析。")
                    self._set_busy(False)
                    if self.batch_active:
                        self.batch_stop_requested = True
                        self._finish_batch_item("已取消", "使用者取消解析，停止後續批次。")
                elif kind == "browser_result":
                    self.browser_auth = {"cookies": payload["cookies"], "user_agent": payload.get("user_agent")}
                    self.msg_queue.put(("resolved", (payload["sources"], payload["page_url"],
                        automation_directory() / "firefox-profile", payload["title"]), self.operation_id))
                    self._write_log(f"[瀏覽器解析完成] 已取得 {len(payload['sources'])} 種畫質")
                elif kind == "resolved":
                    self.sources, self.page_url, self.profile, title = payload
                    self.quality_box["values"] = [quality_label(s) for s in self.sources]
                    self.quality_box.current(0)
                    self.name_var.set(sanitize_filename(title))
                    self.video_var.set(title)
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self.status_var.set("影片已就緒")
                    self.detail_var.set(f"找到 {len(self.sources)} 種畫質，已選擇最高畫質。")
                    self._set_busy(False)
                    if self.batch_active:
                        self.batch_jobs[self.batch_index]["title"] = title
                        self.name_var.set(f"{self.batch_index + 1:03d}_{sanitize_filename(title)}")
                        self.batch_phase = "download"
                        self._batch_status("下載中")
                        self.download()
                elif kind == "progress":
                    percent, detail = payload
                    if percent is not None:
                        self.progress.stop()
                        self.progress.configure(mode="determinate", value=percent)
                        self.percent_var.set(f"{percent:.1f}%")
                    self.detail_var.set(detail or "正在傳輸影片…")
                elif kind == "done":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=100)
                    self.percent_var.set("100%")
                    self.status_var.set("下載完成")
                    self.detail_var.set(str(payload))
                    self._write_log(f"[完成] {payload}")
                    self._set_busy(False)
                    if self.batch_active:
                        self._finish_batch_item("完成", payload)
                elif kind == "error":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self.percent_var.set("")
                    self.status_var.set("無法完成操作")
                    self.detail_var.set("請依照錯誤提示修正後重試，也可以展開紀錄。")
                    self._write_log(f"[錯誤] {payload}")
                    self._set_busy(False)
                    if self.batch_active:
                        self._finish_batch_item("失敗", payload)
                    else:
                        messagebox.showerror(APP_TITLE, str(payload), parent=self)
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def choose_folder(self):
        chosen = filedialog.askdirectory(parent=self, initialdir=self.output_var.get() or str(Path.home()))
        if chosen:
            self.output_var.set(chosen)

    def open_folder(self):
        path = Path(self.output_var.get().strip() or get_downloads_dir()).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(path))
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        except OSError as e:
            messagebox.showerror(APP_TITLE, f"無法開啟資料夾：{e}", parent=self)

    def import_page(self):
        if self.busy or self.batch_active:
            return
        url = self.url_var.get().strip()
        if not is_eeclass_https(url):
            messagebox.showerror(APP_TITLE, "請先填入要下載的 eeClass 影片頁面網址。", parent=self)
            return
        chosen = filedialog.askopenfilename(parent=self, title="選擇 Firefox 儲存的影片頁面原始碼",
            filetypes=[("HTML 網頁", "*.html *.htm"), ("所有檔案", "*.*")])
        if not chosen:
            return
        self.sources = []
        self.page_url = ""
        self.profile = None
        self.browser_auth = None
        self.quality_box.set("")
        self._set_busy(True)
        self.status_var.set("正在匯入影片網頁")
        self.detail_var.set("解析本機 HTML，下載時仍使用 Firefox 登入狀態。")
        self.percent_var.set("")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        def worker():
            try:
                sources, title = read_saved_video_page(chosen, url)
                candidates = firefox_cookie_candidates(url)
                if not candidates:
                    raise RuntimeError("已從 HTML 找到影片，但沒有讀到可供下載的 Firefox 設定檔 Cookie。請提供診斷紀錄。")
                if len(candidates) > 1:
                    raise RuntimeError("已找到影片，但有多個 Firefox 設定檔可供下載。請先用「解析影片」確認正確設定檔，再提供紀錄以設定下載來源。")
                profile = candidates[0][1]
                self.msg_queue.put(("log", f"[匯入 v1.7] 從本機 HTML 找到 {len(sources)} 種畫質；Firefox 設定檔：{profile.name}"))
                self.msg_queue.put(("resolved", (sources, url, profile, title)))
            except Exception as e:
                self.msg_queue.put(("error", str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def resolve(self):
        if self.busy or self.batch_active:
            return
        try:
            urls = collect_video_urls([variable.get() for variable in self.url_vars])
        except ValueError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
            return
        if len(urls) > 1:
            self.start_batch(urls)
        else:
            self._resolve_url(urls[0])

    def _resolve_url(self, url):
        self.operation_id += 1
        operation_id = self.operation_id
        self.sources = []
        self.page_url = ""
        self.profile = None
        self.browser_auth = None
        self.quality_box.set("")
        self._set_busy(True)
        self.status_var.set(f"正在解析 {self.batch_index + 1}/{len(self.batch_jobs)}" if self.batch_active else "正在解析影片")
        self.detail_var.set("自動開啟影片頁面並取得可用畫質…")
        self.percent_var.set("")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.browser_resolving = True
        self._write_log("[自動解析 v1.7] " + url)
        def worker():
            try:
                self.start_browser_worker()
                self.send_browser_command({"op": "resolve", "url": url, "request_id": operation_id})
                self.msg_queue.put(("browser_ready", None, operation_id))
            except Exception as e:
                self.msg_queue.put(("error", str(e), operation_id))
        threading.Thread(target=worker, daemon=True).start()

    def send_browser_command(self, command):
        proc = self.browser_process
        try:
            if proc is None or proc.poll() is not None or proc.stdin is None:
                raise RuntimeError("自動化 Firefox 已關閉，請重新解析。")
            with self.browser_write_lock:
                proc.stdin.write(json.dumps(command) + "\n")
                proc.stdin.flush()
        except (OSError, RuntimeError) as error:
            if not self.closing:
                self.msg_queue.put(("error", str(error), command.get("request_id", self.operation_id)))

    def start_browser_worker(self):
        if self.browser_process is not None and self.browser_process.poll() is None:
            return
        operation_id = self.operation_id
        command = browser_worker_command(lambda text: self.msg_queue.put(("browser_status", text, operation_id)))
        proc = subprocess.Popen(command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", **quiet_process_kwargs())
        self.browser_process = proc

        def reader():
            mapping = {"status": "browser_status", "login_required": "browser_login",
                       "cancelled": "browser_cancelled", "error": "error"}
            try:
                for line in proc.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue  # Driver noise is not UI output; never echo raw IPC.
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "result":
                        self.msg_queue.put(("browser_result", event, event.get("request_id")))
                    elif event.get("type") in mapping:
                        self.msg_queue.put((mapping[event["type"]], event.get("text", ""), event.get("request_id")))
            finally:
                proc.wait()
                if not self.closing and self.browser_resolving and self.browser_process is proc:
                    self.msg_queue.put(("error", "瀏覽器自動化程序已結束，請重新解析。", self.operation_id))
        threading.Thread(target=reader, daemon=True).start()

    def selected_source(self):
        idx = self.quality_box.current()
        return self.sources[idx] if 0 <= idx < len(self.sources) else None

    def copy_mp4(self):
        source = self.selected_source()
        if source:
            self.clipboard_clear()
            self.clipboard_append(source["url"])
            self.status_var.set("已複製 MP4 連結")

    def download(self):
        source = self.selected_source()
        if self.busy or not source or not self.page_url or self.profile is None:
            return
        try:
            executable = find_ytdlp()
            batch_active = getattr(self, "batch_active", False)
            out_dir = self.batch_output_dir if batch_active else Path(self.output_var.get().strip() or get_downloads_dir()).expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)
            output_path = download_output_path(out_dir, self.name_var.get(), source)
            page_url, profile = self.page_url, self.profile
            browser_auth = self.browser_auth
        except (OSError, RuntimeError) as e:
            if getattr(self, "batch_active", False):
                self._finish_batch_item("失敗", str(e))
            else:
                messagebox.showerror(APP_TITLE, str(e), parent=self)
            return
        self.operation_id = getattr(self, "operation_id", 0) + 1
        operation_id = self.operation_id
        self._set_busy(True)
        self.status_var.set(f"正在下載 {self.batch_index + 1}/{len(self.batch_jobs)}" if batch_active else "正在下載 MP4")
        self.detail_var.set("正在連線，請稍候…")
        self.percent_var.set("")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)
        self._write_log(f"[下載] {quality_label(source)} → {output_path}")
        def worker():
            cookie_temp = None
            try:
                cookie_file = None
                user_agent = None
                if browser_auth is not None:
                    cookie_temp = tempfile.TemporaryDirectory(prefix="eeclass_download_")
                    cookie_file = Path(cookie_temp.name) / "cookies.txt"
                    jar = browser_cookie_jar(browser_auth["cookies"])
                    if not list(jar):
                        raise RuntimeError("瀏覽器登入狀態已失效，請重新解析影片後再下載。")
                    jar.save(str(cookie_file), ignore_discard=True, ignore_expires=False)
                    user_agent = browser_auth.get("user_agent")
                cmd = build_download_command(executable, source, page_url, profile, output_path,
                                             cookie_file=cookie_file, user_agent=user_agent)
                with subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) as proc:
                    for line in proc.stdout:
                        progress = parse_progress(line)
                        if progress is not None:
                            self.msg_queue.put(("progress", progress, operation_id))
                        else:
                            self.msg_queue.put(("log", line.rstrip(), operation_id))
                    code = proc.wait()
                if code:
                    raise RuntimeError(f"下載失敗（代碼 {code}）。請展開紀錄查看原因，並確認 Firefox 仍登入 eeClass。")
                if not output_path.is_file() or output_path.stat().st_size == 0:
                    raise RuntimeError("下載程序已結束，但沒有找到完整的 MP4 檔案。請查看紀錄。")
                self.msg_queue.put(("done", output_path, operation_id))
            except Exception as e:
                self.msg_queue.put(("error", str(e), operation_id))
            finally:
                if cookie_temp is not None:
                    cookie_temp.cleanup()
        threading.Thread(target=worker, daemon=True).start()

    def close(self):
        if self.closing:
            return
        if self.busy:
            messagebox.showinfo(APP_TITLE, "請先取消解析，或按「完成此支後停止」並等候目前下載完成，再關閉程式。", parent=self)
            return
        self.closing = True
        proc = self.browser_process
        if proc is None or proc.poll() is not None:
            self.destroy()
            return
        self.status_var.set("正在關閉專用 Firefox")
        self._set_busy(True)
        self.send_browser_command({"op": "quit"})
        deadline = time.monotonic() + 15
        def finish():
            if proc.poll() is not None:
                self.destroy()
            elif time.monotonic() > deadline:
                self.status_var.set("Firefox 尚未關閉")
                self.detail_var.set("請手動關閉程式開啟的 Firefox 視窗，程式會隨後結束。")
                self.after(500, finish)
            else:
                self.after(200, finish)
        self.after(200, finish)


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    app = EeclassDownloaderApp()
    app.mainloop()


def entry_point(args=None):
    args = list(sys.argv[1:] if args is None else args)
    if args and args[0] == "--browser-worker":
        browser_worker_main()
    elif args and args[0] == "--ytdlp-worker":
        import yt_dlp
        yt_dlp.main(args[1:])
    elif args == ["--self-test-browser"]:
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service
        browser, driver_path = prepare_firefox(lambda text: print(text, file=sys.stderr))
        options = Options()
        options.binary_location = browser
        options.add_argument("-headless")
        service = Service(executable_path=driver_path, log_output=subprocess.DEVNULL,
                          popen_kw={"creation_flags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0})
        driver = webdriver.Firefox(options=options, service=service)
        try:
            driver.set_page_load_timeout(30)
            driver.get("about:blank")
            assert driver.execute_script("return 2 + 2") == 4
        finally:
            driver.quit()
        print(json.dumps({"firefox": "ok", "webdriver": "ok"}))
    elif args == ["--self-test"]:
        import selenium
        from selenium.webdriver.common.selenium_manager import SeleniumManager
        from yt_dlp.version import __version__ as yt_version
        manager = SeleniumManager._get_binary()
        if not manager.is_file():
            raise RuntimeError("Missing bundled Selenium Manager")
        subprocess.run([str(manager), "--version"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
                       timeout=30, **quiet_process_kwargs())
        if getattr(sys, "frozen", False):
            # Validate workers launched FROM the frozen parent, sharing its unpacked runtime.
            subprocess.run(browser_worker_command(lambda _: None), input='{"op":"quit"}\n',
                           text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           check=True, timeout=60, **quiet_process_kwargs())
            nested = subprocess.run([*find_ytdlp(), "--version"], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    check=True, timeout=60, **quiet_process_kwargs())
            assert nested.stdout.decode("utf-8").strip() == yt_version
        app = EeclassDownloaderApp()
        try:
            app.withdraw()
            app.url_var.set("https://eeclass.nptu.edu.tw/media/doc/1")
            app.add_url_row()
            app.url_vars[1].set("https://eeclass.nptu.edu.tw/media/doc/2")
            app.add_url_row()
            assert len(collect_video_urls([v.get() for v in app.url_vars])) == 2
            assert "批次下載" in app.resolve_btn.cget("text")
            app._set_busy(True)
            app._set_busy(False)
            app.update_idletasks()
        finally:
            app.destroy()
        print(json.dumps({"application": APP_TITLE, "selenium": selenium.__version__,
                          "yt_dlp": yt_version, "manager": str(manager), "gui": "ok"}))
    else:
        main()


if __name__ == "__main__":
    entry_point()
