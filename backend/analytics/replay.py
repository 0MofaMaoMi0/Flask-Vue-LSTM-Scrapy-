"""python -B -m analytics.replay ; artificial fixtures, not real evaluation."""
import json
from .evaluator import experiment

def main():
 movies=[dict(movie_id=i,title='DEMO '+str(i),catalog_status='active',genres=['g'+str(i%2)],release_year=2000+i,original_language='en') for i in range(1,7)]
 rows=[('demo-a',1,1),('demo-a',2,2),('demo-a',3,3),('demo-b',4,1),('demo-b',5,2),('demo-b',6,3)]
 return experiment(movies,rows,k=2,source='DEMO')
if __name__=='__main__':print(json.dumps(main(),sort_keys=True))
