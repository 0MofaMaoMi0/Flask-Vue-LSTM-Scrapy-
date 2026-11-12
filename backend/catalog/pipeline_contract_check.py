"""M2 deterministic synthetic regression and local API smoke."""
import pathlib,sys,json,copy,csv,subprocess
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from catalog.data_pipeline import build,read_input
from catalog.demo_server import create_demo_app

def main(output):
    out=pathlib.Path(output).resolve()
    if not out.is_dir() or out.drive.upper()=='C:':raise ValueError('Approved output required')
    meta={'source':'tmdb','mode':'SYNTHETIC','license_status':'allowed','license_ref':'SYNTHETIC-GENERATED-NOT-REAL-CINEMA','imported_at':'2026-11-05T00:00:00+00:00'}
    def record(i):return {'source_record_id':'synthetic-'+str(i),'external_ids':{'tmdb':'m2-demo-'+str(i)},'title':'合成电影 '+str(i),'original_title':'SYNTHETIC FILM '+str(i),'aliases':[' 合成别名 '+str(i)],'release_year':2000+i%25,'runtime_minutes':80+i%50,'language':'zh','genres':[['剧情','科幻','冒险'][i%3]],'people':[{'source':'tmdb','external_id':'synthetic-person-'+str(i),'name':'合成影人 '+str(i),'role':'cast'}],'overview':'M2自动生成的合成作品，用于数据流水线和目录演示，不对应真实电影。','ratings':[{'source':'tmdb','value':'7.2','scale':10,'votes':100,'observed_at':meta['imported_at']},{'source':'douban','value':'8.1','scale':10,'votes':200,'observed_at':meta['imported_at']}]}
    records=[record(i) for i in range(1,121)];records.append(copy.deepcopy(records[0]));bad=record(900);bad['runtime_minutes']=-1;records.append(bad)
    for i in (901,902):
        x=record(i);x['external_ids']={'tmdb':'CONFLICT-SYNTHETIC'};records.append(x)
    candidate=record(903);candidate['title']=records[0]['title'];candidate['release_year']=records[0]['release_year'];records.append(candidate)
    env={'metadata':meta,'records':records};(out/'synthetic_input.json').write_text(json.dumps(env,ensure_ascii=False,indent=2),encoding='utf-8')
    checks=[]
    def check(name,condition):
        checks.append({'case':name,'pass':bool(condition)})
        if not condition:raise ValueError(name)
    result=build(env);check('positive records and quarantines',result['quality']['rejected']==1 and result['quality']['quarantined']==2 and result['quality']['duplicates']==1 and result['quality']['review']==1)
    check('repeat idempotence',build(env)==result);shuffled=copy.deepcopy(env);shuffled['records'].reverse();check('input order deterministic',build(shuffled)==result)
    for label,change in [('bad year',{'release_year':1700}),('bad rating',{'ratings':[{'source':'tmdb','value':11,'scale':10,'votes':1,'observed_at':meta['imported_at']}]}),('TV excluded',{'media_type':'tv'}),('private field',{'password':'SYNTHETIC-INVALID'}),('bad date',{'release_date':'not-a-date'}),('invalid source',{'external_ids':{'unknown':'x'}}),('bad person',{'people':[{'name':'no id'}]})]:
        item=record(5);item.update(change);check(label,build({'metadata':meta,'records':[item]})['quality']['rejected']==1)
    empty=record(1);empty['runtime_minutes']=0;empty['release_year']=0;norm=build({'metadata':meta,'records':[empty]})['movies'][0];check('unknown year runtime null',norm['runtime_minutes'] is None and norm['release_year'] is None)
    for status in ('unknown','restricted'):
        m=dict(meta,license_status=status);check('license quarantine '+status,build({'metadata':m,'records':[record(1)]})['quality']['quarantined']==1)
    fullwidth=record(1);fullwidth['title']='  Ａ电影  ';check('NFKC title',build({'metadata':meta,'records':[fullwidth]})['movies'][0]['title']=='A电影')
    legacy=record(1);legacy.update(legacy_table='movies2',legacy_pk=42,external_ids={'douban':'SYNTHETIC-DOUBAN'});check('legacy map preserved',build({'metadata':dict(meta,source='legacy_movies2'),'records':[legacy]})['movies'][0]['legacy']['legacy_pk']==42)
    x=record(1);x['ratings'][0]['votes']=0;check('zero votes score rejected',build({'metadata':meta,'records':[x]})['quality']['rejected']==1)
    csvpath=out/'synthetic_input.csv'
    with csvpath.open('w',encoding='utf-8',newline='') as f:
        fields=list(record(1));writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in record(1).items()})
    check('CSV adapter',build(read_input(csvpath,meta))['quality']['accepted_movies']==1)
    # Run single-command CLI twice against a fresh owned directory.
    product=out/'pipeline-output';product.mkdir(exist_ok=True)
    command=[sys.executable,'-I','-B',str(pathlib.Path(__file__).with_name('data_pipeline.py')),'--input',str(out/'synthetic_input.json'),'--output-dir',str(product)]
    for _ in range(2):check('CLI replay '+str(_),subprocess.run(command,capture_output=True).returncode==0)
    artifact=product/'dataset.json';check('artifact matches normalized batch',json.loads(artifact.read_text(encoding='utf-8'))==result)
    client=create_demo_app(artifact).test_client();response=client.get('/api/catalog/movies?limit=6');check('imported list API',response.status_code==200 and len(response.json['data']['items'])==6)
    movie=response.json['data']['items'][0];detail=client.get('/api/catalog/movies/'+str(movie['movie_id']));check('multi-source score API',len(detail.json['data']['ratings'])==2)
    lookup=client.get('/api/catalog/lookup?source=tmdb&external_id=m2-demo-1');check('external lookup imported identity',lookup.status_code==200)
    check('demo mode declared',client.get('/api/catalog/demo-info').json['mode']=='M2_SYNTHETIC_PIPELINE')
    (out/'pipeline-quality.json').write_text(json.dumps(result['quality'],indent=2),encoding='utf-8');(out/'pipeline-test-results.json').write_text(json.dumps({'checks':checks,'synthetic_only':True,'quality':result['quality'],'api_example':detail.json},ensure_ascii=False,indent=2),encoding='utf-8');print('M2 checks',len(checks),'PASS',result['quality'])
if __name__=='__main__':main(sys.argv[1])
