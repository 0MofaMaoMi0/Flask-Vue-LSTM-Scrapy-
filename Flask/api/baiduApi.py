# -*- coding: utf-8 -*-
"""Baidu OCR integration configured from the local `.env` file."""

import os

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


def get_file_content(file_path):
    with open(file_path, "rb") as file_pointer:
        return file_pointer.read()


def idocr(filename):
    print("当前路径 -> %s" % os.getcwd())
    result = _client().idcard(get_file_content(filename), ID_CARD_SIDE)
    words = result["words_result"]
    return words["公民身份号码"]["words"], words["姓名"]["words"]
