"""Independent S2 disposable offline-container checks; preserves all failures."""
import argparse,json,os,subprocess,sys,time,uuid,io,tarfile,hashlib
from pathlib import Path
import urllib.request

p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--out',required=True)
p.add_argument('--extra-only',action='store_true')
p.add_argument('--regression',action='store_true')
a=p.parse_args();repo=Path(a.repo).resolve();out=Path(a.out).resolve()
if out==repo or repo in out.parents:p.error('--out must be outside result repository')
out.mkdir(parents=True,exist_ok=False)
here=Path(__file__).resolve().parent;revision=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
tag='vf-s2-'+uuid.uuid4().hex[:10]; image=tag+':candidate';network=tag+'-net'; service=tag+'-service'
commands=[]
def run(argv,label,check=True):
    start=time.monotonic();proc=subprocess.run(argv,capture_output=True)
    (out/(label+'.log')).write_bytes(proc.stdout+proc.stderr)
    commands.append({'argv':argv,'label':label,'exit':proc.returncode,'seconds':time.monotonic()-start})
    (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    if check and proc.returncode:raise RuntimeError(label+' failed')
    return proc
snapshot=out/'source';snapshot.mkdir()
archive=subprocess.check_output(['git','-C',str(repo),'archive',revision,'stage-2'])
with tarfile.open(fileobj=io.BytesIO(archive)) as tf:tf.extractall(snapshot,filter='data')
(out/'source-hashes.json').write_text(json.dumps({str(f.relative_to(snapshot)):hashlib.sha256(f.read_bytes()).hexdigest() for f in (snapshot/'stage-2').rglob('*') if f.is_file()},indent=2)+'\n')
run(['docker','build','-t',image,str(snapshot/'stage-2')],'build')
try:
    start=time.monotonic()
    run(['docker','run','-d','--name',service,'--network','none','--cpus=2','--memory=2g','-e','PORT=8080',image],'start')
    url='http://127.0.0.1:8080'
    run(['docker','exec',service,'python','-c','import urllib.request,json; assert json.load(urllib.request.urlopen("http://127.0.0.1:8080/health",timeout=5))=={"status":"ok"}'],'health')
    inspection=json.loads(subprocess.check_output(['docker','inspect',service]))[0]
    constraints={'revision':revision,'healthy_seconds':time.monotonic()-start,'memory':inspection['HostConfig']['Memory'],'nano_cpus':inspection['HostConfig']['NanoCpus'],'network':inspection['HostConfig']['NetworkMode'],'user':inspection['Config']['User'],'image':inspection['Image']}
    (out/'constraints.json').write_text(json.dumps(constraints,indent=2)+'\n')
    failures=[]
    suites=[('cross-feature','stage2_cross_feature.py'),('accessibility','stage2_accessibility.py')] if a.extra_only else [('http-origin','stage2_http_origin.py'),('s1-basic','stage1_acceptance.py'),('s1-expanded','stage1_expanded.py'),('s2-api','stage2_api.py'),('s2-browser','stage2_browser.py')]
    if a.regression:suites=[('http-origin','stage2_http_origin.py'),('s2-browser','stage2_browser.py'),('cross-feature','stage2_cross_feature.py'),('accessibility','stage2_accessibility.py')]
    for label,script in suites:
        command=['docker','run','--rm','--network','container:'+service,'-e','PYTHONDONTWRITEBYTECODE=1','-v',str(here)+':/verification:ro','-v',str(out)+':/evidence','df-harness-runner','python','/verification/'+script,'--url',url,'--revision',revision,'--out','/evidence/'+label]
        if a.regression and script=='stage2_browser.py':command.append('--insecure-host')
        result=run(command,label,False)
        if result.returncode:failures.append(label)
    (out/'summary.json').write_text(json.dumps({'revision':revision,'failures':failures},indent=2)+'\n')
    print(json.dumps({'revision':revision,'failures':failures,'out':str(out)}))
finally:
    run(['docker','logs',service],'service-log',False)
    run(['docker','rm','-f',service],'cleanup-service',False)
    run(['docker','image','rm',image],'cleanup-image',False)
raise SystemExit(1 if failures else 0)
