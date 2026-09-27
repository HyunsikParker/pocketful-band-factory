"""Populated source-destroyed S1->S2 browser migration on offline Docker loopback."""
import argparse,json,subprocess,time,uuid,io,tarfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--out',required=True)
a=p.parse_args();repo=Path(a.repo).resolve();out=Path(a.out).resolve()
if out==repo or repo in out.parents:p.error('--out must be outside result repository')
out.mkdir(parents=True,exist_ok=False)
here=Path(__file__).resolve().parent;revision=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
tag='vf-up-'+uuid.uuid4().hex[:10];target=tag+'-target';source=tag+'-source';images=[tag+':s1',tag+':s2'];actions=[]
def run(argv,label,check=True):
    start=time.monotonic();r=subprocess.run(argv,capture_output=True)
    (out/(label+'.log')).write_bytes(r.stdout+r.stderr)
    actions.append({'argv':argv,'label':label,'exit':r.returncode,'seconds':time.monotonic()-start})
    (out/'commands.json').write_text(json.dumps(actions,indent=2)+'\n')
    if check and r.returncode:raise RuntimeError(label+' failed')
    return r
try:
    snapshot=out/'source';snapshot.mkdir()
    archive=subprocess.check_output(['git','-C',str(repo),'archive',revision,'stage-1','stage-2'])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tf:tf.extractall(snapshot,filter='data')
    for i in (1,2):run(['docker','build','-t',images[i-1],str(snapshot/f'stage-{i}')],f'build-s{i}')
    run(['docker','run','-d','--name',target,'--network','none','--cpus=2','--memory=2g','-e','PORT=8080',images[1]],'target')
    run(['docker','run','-d','--name',source,'--network','container:'+target,'--cpus=2','--memory=2g','-e','PORT=8081',images[0]],'source')
    run(['docker','exec',source,'python','-c','import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:8081/health").status==200'],'health')
    argv=['docker','run','--rm','--network','container:'+target,'-e','PYTHONDONTWRITEBYTECODE=1','-v',str(here)+':/verification:ro','-v',str(out)+':/evidence','df-harness-runner','python','/verification/stage2_upgrade_browser.py','--source-url','http://127.0.0.1:8081','--target-url','http://127.0.0.1:8080','--source-revision','21a65c9bae45cbd8b29eb51f1d2400689517f26e','--target-revision',revision,'--out','/evidence/browser','--source-stop-dir','/evidence/source-stop']
    with (out/'browser.log').open('wb') as log:
        argv.append('--insecure-host')
        proc=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+60;removed=False
        while proc.poll() is None:
            if (out/'source-stop'/'ready').exists() and not removed:
                run(['docker','rm','-f',source],'destroy-source')
                absent=run(['docker','inspect',source],'confirm-source-absent',False)
                assert absent.returncode!=0
                (out/'source-stop'/'removed').write_text('docker rm exit0; docker inspect confirms absent\n');removed=True
            if time.monotonic()>deadline:proc.terminate();raise RuntimeError('upgrade timeout')
            time.sleep(.1)
        assert proc.returncode==0,'browser scenario failed'
        assert removed
    print(json.dumps({'revision':revision,'passed':True,'source_destroyed':True,'out':str(out)}))
finally:
    for name in (source,target):run(['docker','rm','-f',name],'cleanup-'+name,False)
    for i,img in enumerate(images):run(['docker','image','rm',img],'cleanup-image-'+str(i),False)
