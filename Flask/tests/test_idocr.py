"""身份证识别接口的落盘回归测试：照片只在内存中流转，不写 upload/ 目录。"""

import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


FLASK_ROOT = Path(__file__).resolve().parents[1]
if str(FLASK_ROOT) not in sys.path:
    sys.path.insert(0, str(FLASK_ROOT))

os.environ.setdefault("MYSQL_HOST", "127.0.0.1")
os.environ.setdefault("MYSQL_PORT", "3306")
os.environ.setdefault("MYSQL_USER", "smoke")
os.environ.setdefault("MYSQL_PASSWORD", "smoke")
os.environ.setdefault("MYSQL_DATABASE", "smoke")
os.environ.setdefault("SECRET_KEY", "smoke-test-only")

import app as app_module


PNG_BYTES = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 32


class IdOcrNoPersistTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        self.upload_dir = Path(app_module.basedir) / "upload"

    def _uploaded_files(self):
        if not self.upload_dir.is_dir():
            return []
        return sorted(item.name for item in self.upload_dir.iterdir())

    def test_recognized_id_card_is_not_written_to_disk(self):
        before = self._uploaded_files()
        with mock.patch.object(
            app_module, "idocr", return_value=("TEST-IDNO", "TEST-NAME")
        ) as fake_ocr:
            response = self.client.post(
                "/file/idocr",
                data={"myfile": (io.BytesIO(PNG_BYTES), "id-card.png")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["idno"], "TEST-IDNO")
        self.assertEqual(body["data"]["name"], "TEST-NAME")
        self.assertIsNone(body["data"]["pic"])
        self.assertEqual(fake_ocr.call_count, 1)
        self.assertIsInstance(fake_ocr.call_args[0][0], bytes)
        self.assertEqual(before, self._uploaded_files())

    def test_non_image_upload_is_rejected_without_calling_ocr(self):
        before = self._uploaded_files()
        with mock.patch.object(app_module, "idocr") as fake_ocr:
            response = self.client.post(
                "/file/idocr",
                data={"myfile": (io.BytesIO(b"plain text"), "note.txt")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.get_json()["code"], 0)
        self.assertEqual(fake_ocr.call_count, 0)
        self.assertEqual(before, self._uploaded_files())

    def test_missing_file_field_is_rejected(self):
        response = self.client.post("/file/idocr", data={})

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.get_json()["code"], 0)


if __name__ == "__main__":
    unittest.main()