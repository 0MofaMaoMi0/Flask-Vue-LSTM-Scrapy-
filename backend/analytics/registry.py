from datetime import datetime, timezone
VERSION = 'm8.1'
STATUSES = {'OK','NO_DATA','INSUFFICIENT_DATA','UNAVAILABLE','UNKNOWN'}
def metric(name, value, source, scope, window, sample_size, *, status=None, unit='count', denominator=None, notes=''):
    status = status or ('OK' if sample_size else 'NO_DATA')
    assert status in STATUSES
    return dict(metric_id=name,value=value,status=status,source=source,scope=scope,window=window,sample_size=sample_size,as_of=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),definition_version=VERSION,unit=unit,denominator=denominator,notes=notes)
