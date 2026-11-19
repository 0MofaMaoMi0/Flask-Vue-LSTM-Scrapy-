"""Delete recommendation feedback older than the retention window (default 180 days)."""
import argparse, json
from datetime import datetime, timedelta
from sqlalchemy import delete, select,func
from catalog.recommendation_feedback import tables

def purge(engine,schema,days=180,*,now=None,dry_run=False):
    if type(days) is not int or not 30<=days<=730:raise ValueError('days out of range')
    imp,clicks=tables(schema);cutoff=(now or datetime.utcnow())-timedelta(days=days)
    retained=select(clicks.c.click_id).where(clicks.c.user_id==imp.c.user_id,clicks.c.request_id==imp.c.request_id,clicks.c.position==imp.c.position,clicks.c.clicked_at>=cutoff).exists()
    with engine.begin() as c:
        if dry_run:
            a=c.scalar(select(func.count()).select_from(clicks).where(clicks.c.clicked_at<cutoff))
            b=c.scalar(select(func.count()).select_from(imp).where(imp.c.shown_at<cutoff,~retained))
        else:
            a=c.execute(delete(clicks).where(clicks.c.clicked_at<cutoff)).rowcount
            b=c.execute(delete(imp).where(imp.c.shown_at<cutoff,~retained)).rowcount
    return dict(cutoff=cutoff.isoformat()+'Z',dry_run=dry_run,clicks_eligible=a,impressions_eligible=b,clicks_deleted=0 if dry_run else a,impressions_deleted=0 if dry_run else b)

if __name__=='__main__':
    from app import create_app
    ap=argparse.ArgumentParser();ap.add_argument('--days',type=int,default=180);ap.add_argument('--dry-run',action='store_true');args=ap.parse_args()
    app=create_app();print(json.dumps(purge(app.extensions['auth_engine'],app.config['M6_ACTIVITY_SCHEMA'],args.days,dry_run=args.dry_run)))
