#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""



"""

import re
import arrow

def match_year(s):
    matches = re.findall(r'\d{4}', s or '')
    if matches:
        return matches[0]
    else:
        return '0'


def match_date(s):
    matches = re.findall(r'\d{4}-\d{1,2}-\d{1,2}', s or '')
    if matches:
        return matches[0]
    else:
        return False


def str_to_date(s):
    try:
        return str(arrow.get(s, 'YYYY-M-D').format('YYYY-MM-DD'))
    except Exception:
        return False


def is_match_chinese(s):
    matches = re.findall(r'[\u4e00-\u9fa5]+', s or '')
    if matches:
        return True
    else:
        return False


def process_slash_str(s):
    """Normalize slash-separated values without dropping foreign names."""
    items = [item.strip() for item in (s or '').split('/') if item.strip()]
    return '/'.join(items[:30])


def process_url(s):
    if s and len(s) < 255:
        return s
    return ''


def truncate_text(s, max_length=255):
    """Keep text within the legacy MySQL varchar column size."""
    return (s or '').strip()[:max_length]
