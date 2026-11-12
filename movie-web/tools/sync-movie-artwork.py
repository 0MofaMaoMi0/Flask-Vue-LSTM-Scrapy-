"""Explicit capture-to-artwork projection; official fixed image host, no credentials."""
import json,re,time,hashlib,argparse,urllib.request,urllib.parse
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--capture',required=True);parser.add_argument('--api',default='http://127.0.0.1:5174/api/catalog');parser.add_argument('--evidence',required=True);args=parser.parse_args()
repo=Path(__file__).resolve().parents[1];capture=Path(args.capture);evidence=Path(args.evidence);evidence.mkdir(exist_ok=True)
if capture.drive.upper()=='C:' or evidence.drive.upper()=='C:':raise SystemExit('Non-C paths required')
if not re.fullmatch(r'http://127\.0\.0\.1:\d+/api/catalog',args.api):raise SystemExit('Local catalog only')
output=repo/'public/movie-artwork';output.mkdir(exist_ok=True);art={'1':{'poster':'/m9/shaw-poster.jpg','backdrop':'/m9/shaw-hero.jpg'},'2':{'poster':'/m9/inception-poster.jpg','backdrop':'/m9/inception-hero.jpg'}};results=[]
manifest=json.loads((capture/'Capture-Manifest.json').read_text(encoding='utf-8'))
for entry in manifest:
 mid=entry['tmdb_id'];raw=(capture/'raw'/('tmdb-'+str(mid)+'.json')).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise SystemExit('Capture hash mismatch')
 movie=json.loads(raw)
 with urllib.request.urlopen(args.api+'/lookup?'+urllib.parse.urlencode({'source':'tmdb','external_id':mid}),timeout=15) as response:identity=json.load(response)['data']['movie_id']
 paths={}
 for kind,size in [('poster','w500'),('backdrop','w780')]:
  image=movie.get(kind+'_path')
  if not image:continue
  if not re.fullmatch(r'/[A-Za-z0-9_.-]+\.(?:jpg|png|webp)',image):raise SystemExit('Image path rejected')
  url='https://image.tmdb.org/t/p/'+size+image;name='tmdb-'+str(mid)+'-'+kind+Path(image).suffix;file=output/name
  try:
   if not file.exists():
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'MovieInsight academic artwork cache'}),timeout=20) as response:
     if not response.headers.get_content_type().startswith('image/'):raise ValueError('Not image')
     data=response.read(4000001)
    if not 100<len(data)<=4000000 or not (data.startswith(b'\xff\xd8') or data.startswith(b'\x89PNG') or data[:4]==b'RIFF'):raise ValueError('Image size or signature')
    file.write_bytes(data)
   paths[kind]='/movie-artwork/'+name;results.append({'tmdb_id':mid,'movie_id':identity,'kind':kind,'file':name,'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'ok':True})
  except Exception as error:results.append({'tmdb_id':mid,'movie_id':identity,'kind':kind,'ok':False,'error_type':type(error).__name__})
  time.sleep(.5)
 art[str(identity)]=paths
(repo/'src/services/movieArtwork.ts').write_text('// Generated from verified capture + current TMDB/internal ID mapping. Regenerate with tools/sync-movie-artwork.py.\nexport const movieArtwork:Record<string,{poster?:string;backdrop?:string}> = '+json.dumps(art,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(evidence/'artwork-downloads.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'movies_mapped':len(art),'downloaded_or_cached':sum(x['ok'] for x in results),'failures':sum(not x['ok'] for x in results)}))
