"""Root-launched, exact-path/hash approved static-copy import; no legacy input."""
import json
import os
from pathlib import Path
from sqlalchemy import create_engine
from app import create_app
from .m6_mysql import MySQLImporter


def main():
    path=Path(os.environ['MI2_APPROVED_SOURCE_PATH'])
    expected=os.environ['MI2_APPROVED_SOURCE_SHA256']
    app=create_app()
    audit=create_engine(os.environ['MI2_AUDIT_DATABASE_URL'],pool_pre_ping=True,hide_parameters=True)
    importer=MySQLImporter(app.extensions['auth_engine'],audit,app.config,Path(os.environ['MI2_M6_STATE_DIR']))
    result=importer.import_file(path,expected,source_kind='approved_static_copy',approved_real_sources=((str(path),expected),))
    print(json.dumps(result,ensure_ascii=False))
    audit.dispose();app.extensions['auth_engine'].dispose()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'import_passed':False,'error_type':type(error).__name__}))
        raise SystemExit(1)
