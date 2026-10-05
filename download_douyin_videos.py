#!/usr/bin/env python3
"""通过已登录的抖音网页，下载用户主页中当前账号可见的全部视频。"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qs, urlparse

import httpx


class DownloadError(Exception):
    """可直接向用户展示的下载错误。"""


def profile_url(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"MS4wLj[A-Za-z0-9_-]+", value):
        return f"https://www.douyin.com/user/{value}"
    match = re.search(r"https?://[^\s<>\"，。]+", value)
    if not match:
        raise DownloadError("请输入用户主页链接、主页分享短链或 sec_uid（不是数字抖音号）。")
    parsed = urlparse(match.group().rstrip(".,;!?，。；！"))
    if parsed.hostname not in {"www.douyin.com", "douyin.com", "v.douyin.com"}:
        raise DownloadError("链接必须来自 douyin.com 或 v.douyin.com。")
    if parsed.hostname != "v.douyin.com" and not parsed.path.startswith("/user/"):
        raise DownloadError("请提供用户主页链接，而不是单条视频或直播链接。")
    return parsed._replace(scheme="https", fragment="").geturl()


def video_urls(item: dict) -> list[str]:
    video = item.get("video") or {}
    rates = sorted(video.get("bit_rate") or [], key=lambda rate: rate.get("bit_rate", 0), reverse=True)
    addresses = [rate.get("play_addr") or {} for rate in rates]
    addresses += [video.get("play_addr") or {}, video.get("download_addr") or {}]
    urls = []
    for address in addresses:
        for url in address.get("url_list") or []:
            if isinstance(url, str) and url.startswith(("https://", "http://")) and url not in urls:
                urls.append(url)
    return urls


def is_mp4(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 12:
        return False
    with path.open("rb") as handle:
        return handle.read(8)[4:8] == b"ftyp"


def download_video(client: httpx.Client, urls: list[str], target: Path, retries: int) -> bool:
    """只有完整下载后才将 .part 原子替换为 .mp4；返回是否新下载。"""
    if is_mp4(target):
        return False
    if not urls:
        raise DownloadError("作品没有可用的视频地址")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".mp4.part")
    for attempt in range(retries):
        for url in urls:
            try:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if "text/" in content_type or "json" in content_type:
                        raise DownloadError("视频地址返回了非视频内容")
                    received = 0
                    with partial.open("wb") as handle:
                        for chunk in response.iter_bytes(1024 * 1024):
                            handle.write(chunk)
                            received += len(chunk)
                    length = response.headers.get("content-length")
                    if length and not response.headers.get("content-encoding") and received != int(length):
                        raise DownloadError("视频传输不完整")
                    if not is_mp4(partial):
                        raise DownloadError("响应不是有效的 MP4 文件")
                partial.replace(target)
                return True
            except (httpx.HTTPError, DownloadError, ValueError):
                # 不输出包含临时凭据的 CDN URL。
                continue
            finally:
                partial.unlink(missing_ok=True)
        if attempt + 1 < retries:
            time.sleep(min(2 ** attempt, 8))
    raise DownloadError("所有视频地址均下载失败，请重试（可能是网络错误或地址已失效）")


@dataclass
class Stats:
    downloaded: int = 0
    existing: int = 0
    images: int = 0
    failed: int = 0

    def summary(self) -> str:
        return (f"下载 {self.downloaded}，已存在 {self.existing}，"
                f"跳过图文 {self.images}，失败 {self.failed}")


def process_posts(posts: list, seen: set[str], client: httpx.Client,
                  directory: Path, retries: int, stats: Stats) -> None:
    for item in posts:
        if not isinstance(item, dict) or not re.fullmatch(r"\d+", str(item.get("aweme_id", ""))):
            raise DownloadError("作品列表中存在无效的视频 ID，无法确认列表完整性。")
        aweme_id = str(item["aweme_id"])
        if aweme_id in seen:
            continue
        seen.add(aweme_id)
        if item.get("images") or item.get("aweme_type") == 68:
            stats.images += 1
            continue
        try:
            created = download_video(client, video_urls(item), directory / f"{aweme_id}.mp4", retries)
            if created:
                stats.downloaded += 1
                print(f"已下载 {aweme_id}：{str(item.get('desc') or '')[:60]}", flush=True)
            else:
                stats.existing += 1
                print(f"已存在 {aweme_id}", flush=True)
        except DownloadError as exc:
            stats.failed += 1
            print(f"失败 {aweme_id}：{exc}", file=sys.stderr, flush=True)


def validate_page(data: object) -> tuple[list, bool]:
    if not isinstance(data, dict) or data.get("status_code") not in (0, "0"):
        raise DownloadError("抖音作品接口返回错误，请检查浏览器中的登录或验证提示后重试。")
    login_prompt = data.get("not_login_module") or {}
    if data.get("verify_ticket") or (isinstance(login_prompt, dict) and login_prompt.get("guide_login_tip_exist")):
        raise DownloadError("抖音要求登录或验证，无法确认全部作品，请在浏览器中处理后重试。")
    posts = data.get("aweme_list")
    if not isinstance(posts, list) or data.get("has_more") not in (0, 1, "0", "1"):
        raise DownloadError("抖音作品接口返回不完整，无法确认是否已获取全部视频。")
    return posts, data["has_more"] in (1, "1")


def collect_and_download(args: argparse.Namespace, stats: Stats) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DownloadError("请先安装依赖：pip install -r requirements-video.txt；python -m playwright install chromium") from exc

    pending: deque = deque()
    seen: set[str] = set()
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(args.browser_profile.resolve()), headless=False, viewport={"width": 1280, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()

            def on_response(response):
                parsed = urlparse(response.url)
                if parsed.hostname != "www.douyin.com" or parsed.path != "/aweme/v1/web/aweme/post/":
                    return
                uid = parse_qs(parsed.query).get("sec_user_id", [""])[0]
                try:
                    data = response.json() if response.ok else None
                except Exception:
                    data = None
                pending.append((uid, data))

            page.goto(args.user, wait_until="domcontentloaded", timeout=60000)
            print("请在浏览器中完成登录/验证，确认已进入目标用户主页的「作品」页。", flush=True)
            input("准备好后回到终端按 Enter 开始下载：")
            # input 阻塞期间网页仍会运行，先处理浏览器事件以读取最新的主页地址。
            page.wait_for_timeout(200)
            parsed = urlparse(page.url)
            match = re.fullmatch(r"/user/(MS4wLj[A-Za-z0-9_-]+)/?", parsed.path)
            if parsed.hostname not in {"www.douyin.com", "douyin.com"} or not match:
                raise DownloadError("浏览器当前不是用户主页；请使用主页分享链接，不能使用单条视频分享链接。")
            uid = match.group(1)
            directory = args.output / uid
            # 新标签页只接收本轮从第一页开始的请求，避免登录前或手动滚动的旧响应。
            page = context.new_page()
            page.on("response", on_response)
            page.goto(f"https://www.douyin.com/user/{uid}", wait_until="domcontentloaded", timeout=60000)
            deadline = time.monotonic() + args.idle_timeout
            headers = {"User-Agent": page.evaluate("navigator.userAgent"), "Referer": "https://www.douyin.com/"}
            with httpx.Client(headers=headers, follow_redirects=True, timeout=60) as client:
                while True:
                    while pending:
                        response_uid, data = pending.popleft()
                        if response_uid != uid:
                            continue
                        posts, has_more = validate_page(data)
                        before = len(seen)
                        process_posts(posts, seen, client, directory, args.retries, stats)
                        if not has_more:
                            print(f"已到达作品列表末尾。保存目录：{directory.resolve()}", flush=True)
                            return
                        if len(seen) > before:
                            deadline = time.monotonic() + args.idle_timeout
                    if time.monotonic() >= deadline:
                        raise DownloadError("作品列表长时间没有新增内容，未确认全部下载。请检查浏览器验证提示、作品页和网络后重试。")
                    # 抖音不同布局可能由窗口或嵌套容器滚动。
                    page.evaluate("""() => {
                        window.scrollTo(0, document.documentElement.scrollHeight);
                        for (const element of document.querySelectorAll('div, main, section')) {
                            const overflow = getComputedStyle(element).overflowY;
                            if (['auto', 'scroll'].includes(overflow) && element.scrollHeight > element.clientHeight) {
                                element.scrollTop = element.scrollHeight;
                            }
                        }
                    }""")
                    page.mouse.wheel(0, 1800)
                    page.wait_for_timeout(1500)
        finally:
            context.close()


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须为正整数")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user", help="用户主页链接、主页分享文本/短链，或 sec_uid")
    parser.add_argument("-o", "--output", type=Path, default=Path("downloads/douyin_videos"), help="保存目录")
    parser.add_argument("--browser-profile", type=Path, default=Path("downloads/.douyin-browser"), help="浏览器登录资料目录（请勿提交或分享）")
    parser.add_argument("--idle-timeout", type=positive_int, default=90, help="无新增作品的超时秒数，默认 90")
    parser.add_argument("--retries", type=positive_int, default=3, help="视频下载尝试轮数，默认 3")
    args = parser.parse_args(argv)
    stats = Stats()
    result = 0
    try:
        args.user = profile_url(args.user)
        collect_and_download(args, stats)
        result = 1 if stats.failed else 0
    except KeyboardInterrupt:
        print("\n已中断；再次运行会跳过已完整下载的视频。", file=sys.stderr)
        result = 130
    except Exception as exc:
        if isinstance(exc, (DownloadError, OSError, EOFError)):
            message = str(exc)
        else:
            message = "浏览器运行失败，请确认 Chromium 已安装、有图形桌面且登录资料目录未被其他进程占用。"
        print(f"错误：{message}", file=sys.stderr)
        result = 1
    finally:
        print(stats.summary(), flush=True)
    return result


if __name__ == "__main__":
    sys.exit(main())
