"""Sequential immutable-source API regression and S1/S2/S3->S4 migrations."""
import argparse,hashlib,io,json,subprocess,sys,tarfile,time,uuid
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--out',required=True);p.add_argument('--migrations-only',action='store_true',help='Run source-destroyed migrations without repeating already-completed regression suites');a=p.parse_args()
repo=Path(a.repo).resolve();out=Path(a.out).resolve();
if out==repo or repo in out.parents:p.error('--out must be outside result repository')
out.mkdir(parents=True,exist_ok=False)
here=Path(__file__).resolve().parent;revision=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
tag='vf-s4up-'+uuid.uuid4().hex[:10];images={n:tag+':s'+str(n) for n in (1,2,3,4)};service=tag+'-api';actions=[];failures=[]
def run(argv,label,check=True):
 start=time.monotonic();r=subprocess.run(argv,capture_output=True);(out/(label+'.log')).write_bytes(r.stdout+r.stderr)
 actions.append({'argv':argv,'label':label,'exit':r.returncode,'seconds':time.monotonic()-start});(out/'commands.json').write_text(json.dumps(actions,indent=2)+'\n')
 if check and r.returncode:raise RuntimeError(label+' failed')
 return r
try:
 snapshot=out/'source';snapshot.mkdir();archive=subprocess.check_output(['git','-C',str(repo),'archive',revision,'stage-1','stage-2','stage-3','stage-4'])
 with tarfile.open(fileobj=io.BytesIO(archive)) as tf:tf.extractall(snapshot,filter='data')
 for n,img in images.items():run(['docker','build','-t',img,str(snapshot/('stage-'+str(n)))],'build-s'+str(n))
 if not a.migrations_only:
  run(['docker','run','-d','--name',service,'--network','none','--cpus=2','--memory=2g',images[4]],'start-api')
  run(['docker','exec',service,'python','-c','import urllib.request;assert urllib.request.urlopen("http://127.0.0.1:8080/health").status==200'],'health')
  argv=['docker','run','--rm','--network','container:'+service,'-e','PYTHONDONTWRITEBYTECODE=1','-v',str(here)+':/verification:ro','-v',str(out)+':/evidence','df-harness-runner','python','/verification/stage4_api.py','--url','http://127.0.0.1:8080','--revision',revision,'--out','/evidence/api']
  if run(argv,'api',False).returncode:failures.append('api')
  for script,label in [('stage3_import_invariants.py','import-invariants'),('stage3_import_probe.py','original-import-probe'),('stage4_browser.py','browser')]:
   cmd=argv.copy();cmd[cmd.index('/verification/stage4_api.py')]='/verification/'+script;cmd[-1]='/evidence/'+label
   if script=='stage4_browser.py':cmd.append('--insecure-host')
   if run(cmd,label,False).returncode:failures.append(label)
  run(['docker','rm','-f',service],'stop-api')
 for n,source_rev in [(1,'21a65c9bae45cbd8b29eb51f1d2400689517f26e'),(2,'dc2b5f7c2983bde10f83df4ce0d2446328d80f5b'),(3,'5cf06c136945e935a1c90c9bd23bbb946055480b')]:
  argv=[sys.executable,str(here/'stage4_upgrade.py'),'--source-stage',str(n),'--source-image',images[n],'--destination-image',images[4],'--source-revision',source_rev,'--revision',revision,'--out',str(out/('s'+str(n)+'-to-s4'))]
  if run(argv,'upgrade'+str(n),False).returncode:failures.append('upgrade'+str(n))
 (out/'summary.json').write_text(json.dumps({'revision':revision,'failures':failures},indent=2)+'\n');print(json.dumps({'revision':revision,'failures':failures}))
finally:
 run(['docker','rm','-f',service],'cleanup-service',False)
 for n,img in images.items():run(['docker','image','rm',img],'cleanup-image'+str(n),False)
raise SystemExit(bool(failures))
