"""Public metadata cache shared by the scheduler and request-time boards."""
import json, os
from pathlib import Path
from datetime import datetime

def directory():
    return Path(os.environ.get('MI2_TMDB_BOARD_DIR') or os.environ.get('MI2_TRENDING_CACHE') or '/var/lib/movieinsight-m6-realcopy/appdata/trending')

def read(channel):
    try:
        doc=json.loads((directory()/(channel+'.json')).read_text(encoding='utf-8'))
        stamp=datetime.fromisoformat(doc['captured_at'])
        if doc.get('schema')==1 and doc.get('channel')==channel and isinstance(doc.get('items'),list) and stamp.tzinfo is not None:return doc
    except (OSError,ValueError,KeyError,TypeError):pass
