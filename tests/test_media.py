"""媒体解析测试：图片 / 富文本 / 链接 / 文件 / 音视频 / 批量多图。"""
import asyncio
import os
import unittest

from tests import helpers

helpers.patch_lark()

import lark_client

download_calls = []


def fake_download(message_id, file_key, mtype, output_path):
    download_calls.append((message_id, file_key, mtype, output_path))
    return True


lark_client.download_message_resource_sdk = fake_download

from handlers.media import (
    _process_batch_media_message,
    _process_file_audio_media_message,
    _process_image_message,
    _process_link_message,
    _process_post_message,
)
import handlers.media as media_module


class MediaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        helpers.sent_cards.clear()
        download_calls.clear()

    async def test_image(self):
        loop = asyncio.get_running_loop()
        text, fname, ok, reply_id, paths = await _process_image_message(
            loop, "om_1", {"image_key": "img_abc"}, ""
        )
        self.assertIn("img_abc", text)
        self.assertTrue(ok)
        self.assertEqual(len(paths), 1)
        self.assertEqual(download_calls[0][1], "img_abc")
        self.assertEqual(download_calls[0][2], "image")

        # content_raw 正则提取
        text2, _, ok2, _, paths2 = await _process_image_message(
            loop, "om_2", {}, '{"text":"[Image: img_xyz]"}'
        )
        self.assertIn("img_xyz", text2)
        self.assertTrue(ok2)

        # [Image: x] 直接格式
        text3, _, ok3, _, paths3 = await _process_image_message(
            loop, "om_3", {}, "[Image: img_direct]"
        )
        self.assertIn("img_direct", text3)

    async def test_image_download_failure(self):
        loop = asyncio.get_running_loop()
        original = media_module.download_message_resource_sdk
        media_module.download_message_resource_sdk = lambda *a, **k: False
        try:
            _, _, ok, _, paths = await _process_image_message(loop, "om_1", {"image_key": "img_f"}, "")
            self.assertFalse(ok)
            self.assertEqual(paths, [])
        finally:
            media_module.download_message_resource_sdk = original

    async def test_post_with_text_and_image(self):
        loop = asyncio.get_running_loop()
        content = {"content": [[{"tag": "text", "text": "看这张图 "}, {"tag": "img", "image_key": "img_post"}]]}
        text, _, ok, _, paths = await _process_post_message(loop, "om_p", content)
        self.assertIn("看这张图", text)
        self.assertIn("img_post", text)
        self.assertTrue(ok)
        self.assertEqual(len(paths), 1)

    async def test_link(self):
        text, _, ok, _ = await _process_link_message({"url": "https://example.com/a"})
        self.assertEqual(text, "https://example.com/a")
        text2, _, _, _ = await _process_link_message({"href": "https://example.com/b"})
        self.assertEqual(text2, "https://example.com/b")

    async def test_file_audio_media(self):
        loop = asyncio.get_running_loop()
        text, fname, ok, _ = await _process_file_audio_media_message(
            loop, "om_f", "file", {"file_key": "fk1", "file_name": "报告.pdf"}
        )
        self.assertIn("报告.pdf", text)
        self.assertTrue(ok)
        self.assertEqual(download_calls[0][2], "file")

        # 文件名净化（防目录穿越）
        text2, fname2, _, _ = await _process_file_audio_media_message(
            loop, "om_f2", "file", {"file_key": "fk2", "file_name": "../../etc/passwd"}
        )
        self.assertNotIn("..", fname2)
        self.assertEqual(os.path.basename(fname2), fname2)

        # audio 无扩展名补 .ogg
        _, fname3, _, _ = await _process_file_audio_media_message(
            loop, "om_a", "audio", {"file_key": "ak1"}
        )
        self.assertTrue(fname3.endswith(".ogg"))

        # media 强制 .mp4
        _, fname4, _, _ = await _process_file_audio_media_message(
            loop, "om_m", "media", {"file_key": "mk1", "file_name": "video.webm"}
        )
        self.assertTrue(fname4.endswith(".mp4"))

    async def test_batch_media(self):
        loop = asyncio.get_running_loop()
        items = [
            {"message_id": "om_b1", "message_type": "image", "content_json": {"image_key": "img_b1"}, "content_raw": ""},
            {"message_id": "om_b2", "message_type": "file", "content_json": {"file_key": "fk_b2", "file_name": "a.txt"}, "content_raw": ""},
        ]
        text, fname, ok, _, paths = await _process_batch_media_message(loop, "om_b", {"items": items})
        self.assertIn("2 个", text)
        self.assertIn("IMAGE", text)
        self.assertIn("FILE", text)
        self.assertTrue(ok)
        self.assertEqual(len(paths), 1, "只有图片进 image_paths")
