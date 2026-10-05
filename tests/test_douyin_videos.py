"""离线验证下载完整性、分页结束条件和浏览器响应收集。"""
from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import download_douyin_videos as downloader

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 24
UID = "MS4wLjABAAAA_test"


def post(aweme_id):
    return {"aweme_id": str(aweme_id), "video": {"play_addr": {"url_list": ["https://cdn.example/video"]}}}


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_profile_input(self):
        expected = f"https://www.douyin.com/user/{UID}"
        self.assertEqual(downloader.profile_url(UID), expected)
        self.assertEqual(downloader.profile_url(f"来看看我的主页 {expected}"), expected)
        self.assertEqual(downloader.profile_url("我的主页 https://v.douyin.com/abc/"), "https://v.douyin.com/abc/")
        for value in ("12345", "https://example.com/user/test", "https://www.douyin.com/video/1"):
            with self.subTest(value=value), self.assertRaises(downloader.DownloadError):
                downloader.profile_url(value)

    def test_atomic_download_and_skip(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, content=MP4)
        target = self.directory / "1.mp4"
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            self.assertTrue(downloader.download_video(client, ["https://cdn.example/video"], target, 1))
            self.assertFalse(downloader.download_video(client, [], target, 1))
        self.assertEqual(target.read_bytes(), MP4)
        self.assertEqual(len(calls), 1)
        self.assertFalse(target.with_suffix(".mp4.part").exists())

    def test_reject_bad_response_and_try_next_url(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(200, content=MP4 if request.url.path == "/good" else b"<html>denied</html>")
        target = self.directory / "2.mp4"
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            downloader.download_video(client, ["https://cdn.example/bad", "https://cdn.example/good"], target, 1)
        self.assertEqual(calls, ["/bad", "/good"])
        self.assertEqual(target.read_bytes(), MP4)

    def test_incomplete_download_never_becomes_mp4(self):
        target = self.directory / "3.mp4"
        def handler(request):
            return httpx.Response(200, content=MP4, headers={"Content-Length": str(len(MP4) + 1)})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(downloader.DownloadError):
                downloader.download_video(client, ["https://cdn.example/video"], target, 1)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_interrupted_download_cleans_partial(self):
        class Interrupted(httpx.SyncByteStream):
            def __iter__(self):
                yield MP4
                raise KeyboardInterrupt
        def handler(request):
            return httpx.Response(200, stream=Interrupted())
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(KeyboardInterrupt):
                downloader.download_video(client, ["https://cdn.example/video"], self.directory / "4.mp4", 1)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_deduplicate_images_and_continue_after_failure(self):
        stats = downloader.Stats()
        posts = [post(1), post(1), {"aweme_id": "2", "images": [{}]}, {"aweme_id": "3"}, post(4)]
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=MP4))) as client:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                downloader.process_posts(posts, set(), client, self.directory, 1, stats)
        self.assertEqual(stats, downloader.Stats(downloaded=2, images=1, failed=1))
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()), ["1.mp4", "4.mp4"])

    def test_complete_page_required(self):
        self.assertEqual(downloader.validate_page({"status_code": 0, "aweme_list": [], "has_more": 0}), ([], False))
        self.assertEqual(downloader.validate_page({"status_code": 0, "aweme_list": [], "has_more": "1"}), ([], True))
        for data in (None, {}, {"status_code": 2483}, {"status_code": 0, "aweme_list": []},
                     {"status_code": 0, "aweme_list": None, "has_more": 0},
                     {"status_code": 0, "aweme_list": [], "has_more": 0, "verify_ticket": "required"},
                     {"status_code": 0, "aweme_list": [], "has_more": 0,
                      "not_login_module": {"guide_login_tip_exist": True}}):
            with self.subTest(data=data), self.assertRaises(downloader.DownloadError):
                downloader.validate_page(data)

    def test_highest_bitrate_first(self):
        item = {"video": {"bit_rate": [
            {"bit_rate": 100, "play_addr": {"url_list": ["https://cdn.example/low"]}},
            {"bit_rate": 500, "play_addr": {"url_list": ["https://cdn.example/high"]}},
        ], "play_addr": {"url_list": ["https://cdn.example/high", "https://cdn.example/base"]}}}
        self.assertEqual(downloader.video_urls(item), ["https://cdn.example/high", "https://cdn.example/low", "https://cdn.example/base"])

    def test_browser_pagination_and_other_user_ignored(self):
        class Response:
            ok = True
            def __init__(self, uid, items, more):
                self.url = f"https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id={uid}"
                self.data = {"status_code": 0, "aweme_list": items, "has_more": more}
            def json(self):
                return self.data

        class Page:
            url = f"https://www.douyin.com/user/{UID}"
            mouse = SimpleNamespace(wheel=lambda *args: None)
            def on(self, event, callback):
                self.callback = callback
            def goto(self, *args, **kwargs):
                if not hasattr(self, "callback"):
                    return
                self.callback(Response("another-user", [post(999)], 0))
                self.callback(Response(UID, [post(1)], 1))
            def evaluate(self, script):
                return "test-browser" if script == "navigator.userAgent" else None
            def wait_for_timeout(self, duration):
                if hasattr(self, "callback"):
                    self.callback(Response(UID, [post(1), post(2)], 0))

        closed = []
        context = SimpleNamespace(pages=[Page()], new_page=Page, close=lambda: closed.append(True))
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda *a, **kw: context))
        manager = unittest.mock.MagicMock()
        manager.__enter__.return_value = playwright
        module = SimpleNamespace(sync_playwright=lambda: manager)
        args = SimpleNamespace(user=Page.url, output=self.directory, browser_profile=self.directory / "profile",
                               idle_timeout=10, retries=1)
        stats = downloader.Stats()
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=MP4)))
        with patch.dict(sys.modules, {"playwright.sync_api": module}), patch("builtins.input", return_value=""), \
                patch.object(downloader.httpx, "Client", return_value=client), redirect_stdout(io.StringIO()):
            downloader.collect_and_download(args, stats)
        self.assertEqual(stats.downloaded, 2)
        self.assertEqual(sorted(path.name for path in (self.directory / UID).iterdir()), ["1.mp4", "2.mp4"])
        self.assertEqual(closed, [True])

    def test_cli_error_exit_status(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(downloader.main(["12345"]), 1)
            with patch.object(downloader, "collect_and_download", side_effect=KeyboardInterrupt):
                self.assertEqual(downloader.main([UID]), 130)


if __name__ == "__main__":
    unittest.main()
