#!/usr/bin/env python3
"""
CET-4 全真模拟考试系统 — 双文件本地启动器 v2.2（专治 B站 HTTP 412 风控，零第三方依赖）
将本文件与 cet4_exam.html 放在同一目录，双击或执行 `python cet4_launcher.py` 即可。
"""

import gzip
import hashlib
import http.cookiejar
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "cet4_exam.html"
CACHE_DIR = BASE_DIR / "bili_cache"
COOKIE_FILE = BASE_DIR / "bili_cookie.txt"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))
_extra_cookie = ""
_cookie_inited = False
_cookie_lock = threading.Lock()


def make_chrome_headers(referer: str = "https://www.bilibili.com/", is_doc: bool = False) -> dict:
    h = {
        "User-Agent": USER_AGENT,
        "Referer": referer,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip",
        "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    if is_doc:
        h.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        })
    else:
        h.update({
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        })
    if _extra_cookie:
        h["Cookie"] = _extra_cookie
    return h


def ensure_bili_cookies():
    global _cookie_inited, _extra_cookie
    with _cookie_lock:
        if _cookie_inited:
            return
        # 1. Optional user-supplied cookie file (if IP is strictly 412-limited)
        if COOKIE_FILE.exists():
            txt = COOKIE_FILE.read_text(encoding="utf-8", errors="ignore").strip()
            if txt:
                _extra_cookie = txt
                _cookie_inited = True
                print("[提示] 已加载本地 bili_cookie.txt", flush=True)
                return

        # 2. Fetch official buvid3/buvid4 from Bilibili finger/spi endpoint (eliminates 412!)
        b3, b4 = "", ""
        try:
            req = urllib.request.Request(
                "https://api.bilibili.com/x/frontend/finger/spi",
                headers=make_chrome_headers("https://www.bilibili.com/", is_doc=False),
            )
            with _opener.open(req, timeout=12) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                spi = json.loads(raw.decode("utf-8"))
                d = spi.get("data") or {}
                b3 = d.get("b_3", "")
                b4 = d.get("b_4", "")
        except Exception as e:
            print(f"[提示] spi 获取 buvid 失败，使用本地生成 buvid3: {e}", flush=True)

        if not b3:
            b3 = f"{str(uuid.uuid4()).upper()}{random.randint(10000, 99999)}infoc"
        parts = [f"buvid3={b3}", f"b_nut={int(time.time())}", "CURRENT_FNVAL=4048"]
        if b4:
            parts.append(f"buvid4={b4}")
        _extra_cookie = "; ".join(parts)
        _cookie_inited = True


def http_get_bytes(url: str, referer: str = "https://www.bilibili.com/", timeout: int = 25, is_doc: bool = False) -> bytes:
    req = urllib.request.Request(url, headers=make_chrome_headers(referer, is_doc=is_doc))
    with _opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return raw


def extract_urls_from_playinfo(play_data: dict) -> list:
    urls = []
    pdata = play_data.get("data") if isinstance(play_data.get("data"), dict) else play_data
    dash = pdata.get("dash") or {}
    audios = sorted((dash.get("audio") or []), key=lambda x: x.get("bandwidth", 999999999))
    for a in audios:
        for k in ("baseUrl", "base_url"):
            if a.get(k):
                urls.append(a[k])
        for k in ("backupUrl", "backup_url"):
            for bu in (a.get(k) or []):
                if bu:
                    urls.append(bu)
    for d in (pdata.get("durl") or []):
        if d.get("url"):
            urls.append(d["url"])
        for bu in (d.get("backup_url") or []):
            if bu:
                urls.append(bu)
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def strategy_html_playinfo(bvid: str, page_num: int) -> list:
    page_url = f"https://www.bilibili.com/video/{bvid}?p={page_num}"
    html = http_get_bytes(page_url, referer="https://www.bilibili.com/", timeout=20, is_doc=True).decode("utf-8", errors="ignore")
    m = re.search(r"window\.__playinfo__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        raise RuntimeError("页面未包含 __playinfo__")
    info = json.loads(m.group(1))
    urls = extract_urls_from_playinfo(info)
    if not urls:
        raise RuntimeError("__playinfo__ 中未找到音频流")
    return urls


def get_wbi_mixin_key() -> str:
    raw = http_get_bytes("https://api.bilibili.com/x/web-interface/nav", timeout=15)
    nav = json.loads(raw.decode("utf-8"))
    wbi = (nav.get("data") or {}).get("wbi_img") or {}
    img_key = wbi.get("img_url", "").rsplit("/", 1)[-1].split(".")[0]
    sub_key = wbi.get("sub_url", "").rsplit("/", 1)[-1].split(".")[0]
    orig = img_key + sub_key
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB if i < len(orig))[:32]


def sign_wbi(params: dict, mixin_key: str) -> str:
    params = dict(params)
    params["wts"] = str(int(time.time()))
    cleaned = {k: re.sub(r"[!'()*]", "", str(params[k])) for k in sorted(params.keys())}
    query = urllib.parse.urlencode(cleaned)
    w_rid = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    return f"{query}&w_rid={w_rid}"


def resolve_cid(bvid: str, page_num: int) -> int:
    raw = http_get_bytes(f"https://api.bilibili.com/x/web-interface/view?bvid={urllib.parse.quote(bvid)}", timeout=15)
    view_data = json.loads(raw.decode("utf-8"))
    if view_data.get("code") != 0:
        raise RuntimeError(f"view 接口错误: {view_data.get('message')}")
    data = view_data.get("data") or {}
    cid = data.get("cid", 0)
    for pg in (data.get("pages") or []):
        if int(pg.get("page", 0)) == page_num:
            cid = pg.get("cid", cid)
            break
    if not cid:
        raise RuntimeError("未找到对应分P cid")
    return int(cid)


def strategy_wbi_playurl(bvid: str, page_num: int) -> list:
    cid = resolve_cid(bvid, page_num)
    mixin = get_wbi_mixin_key()
    qs = sign_wbi({"bvid": bvid, "cid": cid, "fnval": 16, "fnver": 0, "fourk": 0}, mixin)
    raw = http_get_bytes(f"https://api.bilibili.com/x/player/wbi/playurl?{qs}", timeout=20)
    data = json.loads(raw.decode("utf-8"))
    if data.get("code") != 0:
        raise RuntimeError(f"wbi playurl 错误: {data.get('message')}")
    urls = extract_urls_from_playinfo(data)
    if not urls:
        raise RuntimeError("wbi playurl 未返回音频流")
    return urls


def strategy_html5_playurl(bvid: str, page_num: int) -> list:
    cid = resolve_cid(bvid, page_num)
    url = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=64&fnval=1&platform=html5&high_quality=1"
    raw = http_get_bytes(url, timeout=20)
    data = json.loads(raw.decode("utf-8"))
    urls = extract_urls_from_playinfo(data)
    if not urls:
        raise RuntimeError("html5 playurl 未返回流")
    return urls


def download_stream_to_file(urls: list, referer: str, cache_file: Path) -> bool:
    tmp_file = cache_file.with_suffix(".tmp")
    last_err = None
    for idx, u in enumerate(urls[:6]):
        try:
            headers = {
                "User-Agent": USER_AGENT,
                "Referer": referer,
                "Origin": "https://www.bilibili.com",
                "Accept": "*/*",
            }
            req = urllib.request.Request(u, headers=headers)
            with _opener.open(req, timeout=60) as resp, open(tmp_file, "wb") as out:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
            if tmp_file.exists() and tmp_file.stat().st_size > 8192:
                tmp_file.replace(cache_file)
                return True
        except Exception as e:
            last_err = e
            print(f"[线路 {idx+1} 失败] {e}", flush=True)
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
    raise RuntimeError(f"音频流下载失败: {last_err}")


def strategy_ytdlp(bvid: str, page_num: int, cache_file: Path) -> bool:
    yt = shutil.which("yt-dlp")
    if not yt:
        return False
    page_url = f"https://www.bilibili.com/video/{bvid}?p={page_num}"
    tmp_out = str(cache_file.with_suffix(".yt.%(ext)s"))
    for extra in ([], ["--cookies-from-browser", "edge"], ["--cookies-from-browser", "chrome"]):
        cmd = [yt, "-f", "bestaudio[ext=m4a]/bestaudio", "-o", tmp_out, "--no-playlist", *extra, page_url]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            for cand in cache_file.parent.glob(cache_file.stem + ".yt.*"):
                if cand.stat().st_size > 8192:
                    cand.replace(cache_file)
                    return True
    return False


def download_bili_audio(bvid: str, page_num: int) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_bvid = re.sub(r"[^A-Za-z0-9_-]", "", bvid)
    cache_file = CACHE_DIR / f"{safe_bvid}_p{page_num}.m4a"
    if cache_file.exists() and cache_file.stat().st_size > 8192:
        print(f"[命中本地缓存] {cache_file.name}", flush=True)
        return cache_file

    ensure_bili_cookies()
    referer = f"https://www.bilibili.com/video/{safe_bvid}?p={page_num}"
    errors = []

    for name, fn in [
        ("页面__playinfo__", strategy_html_playinfo),
        ("WBI签名接口", strategy_wbi_playurl),
        ("HTML5直链接口", strategy_html5_playurl),
    ]:
        try:
            print(f"[拉取 B站听力 {safe_bvid} P{page_num}] 尝试策略: {name}...", flush=True)
            urls = fn(safe_bvid, page_num)
            if download_stream_to_file(urls, referer, cache_file):
                print(f"[下载成功] {cache_file.name} ({cache_file.stat().st_size // 1024} KB)", flush=True)
                return cache_file
        except Exception as e:
            print(f"[{name} 失败] {e}", flush=True)
            errors.append(f"{name}: {e}")

    try:
        if strategy_ytdlp(safe_bvid, page_num, cache_file):
            return cache_file
    except Exception as e:
        errors.append(f"yt-dlp: {e}")

    raise RuntimeError("；".join(errors[-2:]))


class CET4Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_bytes(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_audio_with_range(self, file_path: Path):
        size = file_path.stat().st_size
        range_hdr = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if range_hdr and range_hdr.startswith("bytes="):
            m = re.match(r"bytes=(\d*)-(\d*)", range_hdr)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                if start <= end < size:
                    status = 206

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "audio/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html", "/cet4_exam.html"):
            if not HTML_FILE.exists():
                self._send_bytes(404, "text/plain; charset=utf-8", "未找到 cet4_exam.html，请确认与启动器在同一目录。".encode("utf-8"))
                return
            self._send_bytes(200, "text/html; charset=utf-8", HTML_FILE.read_bytes())
            return

        if parsed.path == "/api/ping":
            self._send_bytes(200, "application/json", b'{"ok":true,"biliProxy":true}')
            return

        if parsed.path == "/api/bili-audio":
            qs = urllib.parse.parse_qs(parsed.query)
            bvid = (qs.get("bvid") or [""])[0].strip()
            p_str = (qs.get("p") or [""])[0].strip()
            if not bvid or not p_str.isdigit() or int(p_str) < 1:
                self._send_bytes(400, "text/plain; charset=utf-8", b"invalid bvid or page")
                return
            try:
                audio_path = download_bili_audio(bvid, int(p_str))
                self._serve_audio_with_range(audio_path)
            except Exception as e:
                print(f"[音频拉取失败] {bvid} P{p_str}: {e}", flush=True)
                self._send_bytes(502, "text/plain; charset=utf-8", str(e).encode("utf-8"))
            return

        self._send_bytes(404, "text/plain; charset=utf-8", b"Not Found")


def main():
    if not HTML_FILE.exists():
        print("错误：当前目录下未找到 cet4_exam.html，请将两个文件放在同一文件夹内。")
        input("按回车键退出...")
        sys.exit(1)

    server = ThreadingHTTPServer(("127.0.0.1", 0), CET4Handler)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print("=" * 56)
    print("  CET-4 全真模拟考试系统 v2.2 本地启动器（防 412 版）")
    print(f"  访问地址: {url}")
    print("  如遇网络 412 风控，可在同目录放 bili_cookie.txt 粘贴 B站 Cookie。")
    print("  考试期间请保持本窗口开启，考完直接关闭即可。")
    print("=" * 56, flush=True)

    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出 CET-4 本地服务。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
