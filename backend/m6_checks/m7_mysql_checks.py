"""Stable fixed sudo-check entry point; V1 evidence is retained in the baseline."""
import json
from m6_checks.m7_byok_mysql_checks import main,checks
if __name__=='__main__':
    try:main();print(json.dumps({'all_pass':True,'checks':checks}))
    except Exception as error:print(json.dumps({'all_pass':False,'checks':checks,'error_type':type(error).__name__}));raise SystemExit(1)
