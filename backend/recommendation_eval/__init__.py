"""Offline, standard-library-only recommendation evaluation for M10.

Nothing in this package is imported by the Flask application. It reads frozen
JSON inputs and calls a caller-supplied ranker adapter; it never opens a
database, network connection, model provider or user session.
"""

EVAL_SCHEMA_VERSION = "m10-eval-v1"
METRIC_VERSION = "m10-metrics-v1"
BASELINE_ID = "baseline-v1-catalog-recommend-20261113"

__all__ = ["EVAL_SCHEMA_VERSION", "METRIC_VERSION", "BASELINE_ID"]