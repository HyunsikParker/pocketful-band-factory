"""Portable Docker runner for independent stage-1 verification.

Uses the caller's existing Docker context, without changing daemon settings.
All reports go to a fresh caller-selected output directory, outside this repo.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    repo = Path(args.repo).absolute()
    out = Path(args.out).absolute()
    if out == repo or repo in out.parents:
        parser.error('--out must be outside the result repository')
    out.mkdir(parents=True, exist_ok=False)
    revision = subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    subprocess.run(['git','-C',str(repo),'diff','--exit-code',revision,'--','stage-1'],check=True)
    tag = uuid.uuid4().hex[:12]
    image = 'pocketful-verifier:'+tag
    service = 'pocketful-verifier-'+tag
    here = Path(__file__).resolve().parent
    actions = []

    def command(argv, label, check=True):
        started = time.monotonic()
        proc = subprocess.run(argv,capture_output=True)
        (out/(label+'.log')).write_bytes(proc.stdout+proc.stderr)
        actions.append({'command':argv,'label':label,'exit':proc.returncode,'seconds':time.monotonic()-started})
        (out/'commands.json').write_text(json.dumps(actions,indent=2)+'\n')
        if check and proc.returncode:
            raise RuntimeError(label+' failed; inspect '+str(out/(label+'.log')))
        return proc

    command(['docker','build','-t',image,str(repo/'stage-1')],'build')
    running = False
    try:
        started = time.monotonic()
        command(['docker','run','-d','--name',service,'--network','none','--cpus=2','--memory=2g','-e','PORT=9090',image],'start')
        running = True
        health = 'import urllib.request,json; r=urllib.request.urlopen("http://127.0.0.1:9090/health",timeout=5); assert r.status==200; assert json.load(r)=={"status":"ok"}; print("ok")'
        command(['docker','exec',service,'python','-c',health],'health')
        health_seconds = time.monotonic()-started
        assert health_seconds < 60
        state = json.loads(subprocess.check_output(['docker','inspect',service]))[0]
        constraints = {'revision':revision,'healthy_seconds':health_seconds,'network':state['HostConfig']['NetworkMode'],
                       'memory':state['HostConfig']['Memory'],'nano_cpus':state['HostConfig']['NanoCpus'],'image':state['Image'],'user':state['Config']['User']}
        assert constraints['network']=='none' and constraints['memory']==2147483648 and constraints['nano_cpus']==2000000000
        (out/'constraints.json').write_text(json.dumps(constraints,indent=2)+'\n')
        command(['docker','exec','--user','0',service,'mkdir','-p','/verification'],'make-test-directory')
        for source in ('stage1_acceptance.py','stage1_expanded.py'):
            command(['docker','cp',str(here/source),service+':/verification/'+source],'copy-'+source)
        failures = []
        for label, source in [('basic','stage1_acceptance.py'),('expanded','stage1_expanded.py')]:
            container_out = '/verification-output/'+label
            tested = command(['docker','exec','--user','0','-w','/verification',service,'python',source,'--url','http://127.0.0.1:9090','--revision',revision,'--out',container_out],label,check=False)
            command(['docker','cp',service+':'+container_out,str(out/label)],'retrieve-'+label)
            if tested.returncode:
                failures.append(label)
        command(['docker','logs',service],'service-log')
        command(['docker','rm','-f',service],'remove-service')
        running = False
        portable = command([sys.executable,str(here/'cross_process_import.py'),'--out',str(out/'portability'),'--image',image,'--revision',revision],'portability',check=False)
        if portable.returncode:
            failures.append('portability')
        (out/'summary.json').write_text(json.dumps({'revision':revision,'failure_groups':failures,'constraints':constraints},indent=2)+'\n')
        print(json.dumps({'revision':revision,'failure_groups':failures,'out':str(out)}))
        return 1 if failures else 0
    finally:
        if running:
            command(['docker','logs',service],'final-service-log',check=False)
            command(['docker','rm','-f',service],'cleanup-service',check=False)
        command(['docker','image','rm',image],'cleanup-owned-image',check=False)


if __name__ == '__main__':
    raise SystemExit(main())
