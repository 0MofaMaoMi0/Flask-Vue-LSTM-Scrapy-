"""Publish verified regional-release evidence. Does not start privileged imports."""
import hashlib,json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
WORK=ROOT/'项目工作日志/06-开发与验证/M9-全库演职员补充-20261112'
seal_path=WORK/'newmatches-sealed.json'
while not seal_path.exists():time.sleep(5)
seal=json.loads(seal_path.read_text(encoding='utf-8'))
if not seal['movies']:raise SystemExit('NO_CORROBORATED_NEWMATCHES_DOCUMENTS')
bundle=WORK/'m9-cast-newmatches.tar'
if hashlib.sha256(bundle.read_bytes()).hexdigest()!=seal['sha256']:raise RuntimeError('LOCAL_BUNDLE_MISMATCH')
ssh=['ssh.exe','-F','none','-i','F:/M6-VM-control-20261106-183615/private/m6_ed25519','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','HostKeyAlias=192.168.147.131','-o','UserKnownHostsFile=F:/M6-VM-control-20261106-183615/private/known_hosts','zhang@192.168.147.50']
for source,target in [(bundle,'m9-cast-newmatches.tar.upload'),(Path(__file__).with_name('import_cast_newmatches.py'),'import-m9-cast-newmatches.py')]:
    subprocess.run(['scp.exe',*ssh[1:-1],str(source),'zhang@192.168.147.50:/home/zhang/'+target],check=True)
code="""from pathlib import Path
import hashlib,json,os
base=Path.home();source=base/'m9-cast-newmatches.tar.upload';seal=json.loads(%r)
if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest()!=seal['sha256']:raise RuntimeError('UPLOAD_MISMATCH')
source.chmod(0o600);os.replace(source,base/'m9-cast-newmatches.tar')
tmp=base/'m9-cast-newmatches.ready.upload';tmp.write_text(json.dumps(seal));tmp.chmod(0o600);os.replace(tmp,base/'m9-cast-newmatches.ready.json')
(base/'import-m9-cast-newmatches.py').chmod(0o600)
print('NEWMATCHES_PACKAGE_READY_NOT_EXECUTED')
"""%json.dumps(seal)
subprocess.run(ssh+['python3 -'],input=code.encode(),check=True)
print(json.dumps({'verified_movies':seal['movies'],'requires_guest_sudo':True}),flush=True)
