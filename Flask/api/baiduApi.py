# -*- coding: utf-8 -*-
"""Baidu OCR integration configured from the local `.env` file."""

from aip import AipOcr

from config import required


ID_CARD_SIDE = "back"


def _client():
    """Construct the OCR client only when an OCR request is made."""
    return AipOcr(
        required("BAIDU_OCR_APP_ID"),
        required("BAIDU_OCR_API_KEY"),
        required("BAIDU_OCR_SECRET_KEY"),
    )


def idocr(image_bytes):
    """识别身份证：直接接收图片字节，全程不落盘。

    调用方只把上传流读进内存，识别结束随请求释放；身份证照片既不会写进
    Flask/upload/ 目录，也不会留在磁盘缓存里。
    """
    result = _client().idcard(image_bytes, ID_CARD_SIDE)
    words = result["words_result"]
    return words["公民身份号码"]["words"], words["姓名"]["words"]
