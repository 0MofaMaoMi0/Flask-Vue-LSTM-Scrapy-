"""Delete recommendation feedback older than the retention window (default 180 days)."""
import argparse
from datetime import datetime, timedelta
from sqlalchemy import delete
from app import create_app
from catalog.recommendation_feedback import tables

ap = argparse.ArgumentParser()
ap.add_argument('--days', type=int, default=180)
args = ap.parse_args()
if not 30 <= args.days <= 730:
    raise SystemExit('days out of range')
app = create_app()
engine = app.extensions['auth_engine']
imp, clicks = tables(app.config['M6_ACTIVITY_SCHEMA'])
cutoff = datetime.utcnow() - timedelta(days=args.days)
with engine.begin() as c:
    a = c.execute(delete(clicks).where(clicks.c.clicked_at < cutoff)).rowcount
    b = c.execute(delete(imp).where(imp.c.shown_at < cutoff)).rowcount
print({'cutoff': cutoff.isoformat() + 'Z', 'clicks_deleted': a, 'impressions_deleted': b})
