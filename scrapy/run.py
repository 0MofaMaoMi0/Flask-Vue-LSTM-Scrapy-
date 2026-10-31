#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Small cross-platform wrapper around scrapy crawl."""

import os
import sys
from pathlib import Path

from scrapy.cmdline import execute


SPIDERS = {'movie_subject', 'movie_meta', 'movie_comment', 'person_item'}


def main():
    spider = sys.argv[1] if len(sys.argv) > 1 else 'movie_subject'
    if spider not in SPIDERS:
        choices = ', '.join(sorted(SPIDERS))
        raise SystemExit('Unknown spider {!r}. Choices: {}'.format(spider, choices))
    os.chdir(Path(__file__).resolve().parent)
    execute(['scrapy', 'crawl', spider] + sys.argv[2:])


if __name__ == '__main__':
    main()

