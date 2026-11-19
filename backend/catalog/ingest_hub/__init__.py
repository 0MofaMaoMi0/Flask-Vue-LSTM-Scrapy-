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
from .douban_adapter import (
    DOUBAN_ADAPTER_VERSION,
    build_douban_archive,
    classify_douban_item,
    douban_item_to_envelope_record,
    load_douban_raw,
)

_PIPELINE_EXPORTS = frozenset((
    'commit_archive',
    'convert_catalog_to_supplement',
    'run_capture',
    'summary_envelope',
    'verify_archive',
))
_SELECTOR_EXPORTS = frozenset((
    'load_plan',
    'select_targets',
))
_DISCOVER_EXPORTS = frozenset((
    'discover_catalog_targets',
    'export_existing_tmdb_ids',
))


def __getattr__(name):
    if name in _PIPELINE_EXPORTS:
        from . import pipeline as _pipeline
        return getattr(_pipeline, name)
    if name in _SELECTOR_EXPORTS:
        from . import selector as _selector
        return getattr(_selector, name)
    if name in _DISCOVER_EXPORTS:
        from . import discover as _discover
        return getattr(_discover, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


__all__ = [
    'DOUBAN_ADAPTER_VERSION',
    'HUB_CONTRACT',
    'RULE_VERSION',
    'SUPPORTED_SOURCES',
    'HubError',
    'HubInputError',
    'HubRejected',
    'HubSecurityError',
    'build_douban_archive',
    'classify_douban_item',
    'commit_archive',
    'convert_catalog_to_supplement',
    'discover_catalog_targets',
    'douban_item_to_envelope_record',
    'export_existing_tmdb_ids',
    'load_douban_raw',
    'load_plan',
    'run_capture',
    'select_targets',
    'summary_envelope',
    'validate_archive_dir',
    'validate_env_name',
    'validate_job_id',
    'verify_archive',
]
