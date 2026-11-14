import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from protected_regression_runner import policy, configured_test_url

# Validation only: no file fallback, environment mutation, app creation or connection.
configured_test_url()

def pytest_sessionstart(session):
    policy()
