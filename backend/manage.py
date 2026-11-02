"""Local launcher: reads only the dedicated, untracked authentication config."""
import os
import sys
from pathlib import Path

def configure(test=False):
    path=Path(__file__).resolve().parents[2]/'敏感信息调用'/'phase1b-auth.local.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line:
                key,value=line.split('=',1)
                if key in ('MI2_DATABASE_URL','MI2_TEST_DATABASE_URL'):os.environ.setdefault(key,value)
    if test:
        os.environ['MI2_DATABASE_URL']=os.environ.get('MI2_TEST_DATABASE_URL','')
    return test

if __name__=='__main__':
    test=configure('--test' in sys.argv)
    from app import create_app
    create_app({'TESTING':test}).run(host='127.0.0.1',port=int(os.environ.get('MI2_PORT','8091')),debug=False)
