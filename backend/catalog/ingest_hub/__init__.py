"""Unified declarative data capture, offline verification and transactional commit hub."""
from .contracts import (
    HUB_CONTRACT,
    RULE_VERSION,
    SUPPORTED_SOURCES,
    HubError,
    HubInputError,
    HubRejected,
    HubSecurityError,
    validate_archive_dir,
    validate_env_name,
    validate_job_id,
)
from .pipeline import (
    commit_archive,
    run_capture,
    summary_envelope,
    verify_archive,
)
from .selector import load_plan, select_targets

__all__ = [
    'HUB_CONTRACT',
    'RULE_VERSION',
    'SUPPORTED_SOURCES',
    'HubError',
    'HubInputError',
    'HubRejected',
    'HubSecurityError',
    'commit_archive',
    'load_plan',
    'run_capture',
    'select_targets',
    'summary_envelope',
    'validate_archive_dir',
    'validate_env_name',
    'validate_job_id',
    'verify_archive',
]
