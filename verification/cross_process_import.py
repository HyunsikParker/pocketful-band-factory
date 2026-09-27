"""Portable populated export: destroy source container before importing destination.

Export bytes and synthetic credentials remain only in subprocess memory/stdin.
"""
import json
import os
from pathlib import Path
import subprocess
import time

import argparse
import uuid

parser = argparse.ArgumentParser()
parser.add_argument("--out", required=True)
parser.add_argument("--image", required=True)
parser.add_argument("--revision", required=True)
args = parser.parse_args()
OUT = Path(args.out).absolute()
OUT.mkdir(parents=True, exist_ok=False)
ENV = os.environ.copy()
run_id = uuid.uuid4().hex[:12]
SOURCE = "verifier-source-" + run_id
DEST = "verifier-destination-" + run_id
REVISION = args.revision
HERE = Path(__file__).parent


def run(args, label, data=None, private=False):
    began = time.monotonic()
    p = subprocess.run(args,input=data,capture_output=True,env=ENV)
    if not private:
        (OUT/(label+'.log')).write_bytes(p.stdout+p.stderr)
    with (OUT/'operations.jsonl').open('a') as stream:
        stream.write(json.dumps({'label':label,'exit':p.returncode,'seconds':time.monotonic()-began})+'\n')
    assert p.returncode == 0, label+' failed; private outputs suppressed by design' if private else label+' failed'
    return p.stdout


try:
    for name in (SOURCE,DEST):
        run(['docker','run','-d','--name',name,'--network','none','--cpus=2','--memory=2g',args.image], 'start-'+name)
        run(['docker','cp',str(HERE/'stage1_acceptance.py'),name+':/app/stage1_acceptance.py'], 'helper-'+name)
    make_source = '''import json,concurrent.futures
import stage1_acceptance as b
b.BASE='http://127.0.0.1:8080'
assert b.http('POST','/_test/reset',b.fixture())[0]==204
tokens={}
for who in ['ada','bob','cy','op']:
    s,r=b.http('POST','/auth/login',{'email':who+'@example.test','password':b.PASSWORD}); assert s==200; tokens[who]=r['token']
body={'transfers':[{'from_handle':'ada','to_handle':'bob','amount':3,'visibility':'private'},{'from_handle':'bob','to_handle':'cy','amount':2}]}
s,settlement=b.http('POST','/settlements',body,tokens['op'],'persist-settlement'); assert s==201
sb={'amount':1,'participant_handles':['ada','bob','cy']}
s,split=b.http('POST','/splits',sb,tokens['ada'],'persist-split'); assert s==201
s,request=b.http('POST','/requests',{'payer_handle':'ada','amount':4},tokens['bob'],'persist-request'); assert s==201
s,payment=b.http('POST','/requests/'+request['request_id']+'/pay',{'visibility':'private'},tokens['ada'],'persist-pay'); assert s==201
def login(_):
    return b.http('POST','/auth/login',{'email':'ada@example.test','password':b.PASSWORD})[0]
with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
    assert list(pool.map(login,range(50))) == [200]*50
s,export=b.http('GET','/_test/export'); assert s==200
print(json.dumps({'export':export,'tokens':tokens,'settlement':settlement,'settlement_body':body,'split':split,'split_body':sb,'request':request,'payment':payment,'max_source_request_seconds':max(x['seconds'] for x in b.REQUEST_TIMES)}))
'''
    package = run(['docker','exec','-i',SOURCE,'python','-'],'populate-export',make_source.encode(),private=True)
    run(['docker','rm','-f',SOURCE],'destroy-source-before-import')
    accept_dest = '''import json,sys
import stage1_acceptance as b
b.BASE='http://127.0.0.1:8080'
p=json.load(sys.stdin)
assert b.http('POST','/_test/reset',b.fixture('JPY',0))[0]==204
s,new=b.http('POST','/auth/signup',{'email':'only-dest@example.test','password':b.PASSWORD,'display_name':'Dest'}); assert s==201
for n in range(2):
    assert b.http('POST','/_test/import',p['export'])[0]==204
    assert b.http('GET','/me',token=new['token'])[0]==401
    total=0
    for who,token in p['tokens'].items():
        s,me=b.http('GET','/me',token=token); assert s==200 and me['currency']=='EUR' and me['minor_units']==2; total+=me['balance']
    assert total==300
    assert b.http('POST','/settlements',p['settlement_body'],p['tokens']['op'],'persist-settlement')==(200,p['settlement'])
    assert b.http('POST','/splits',p['split_body'],p['tokens']['ada'],'persist-split')==(200,p['split'])
    assert b.http('POST','/requests/'+p['request']['request_id']+'/pay',{'visibility':'private'},p['tokens']['ada'],'persist-pay')==(200,p['payment'])
    assert b.http('POST','/auth/login',{'email':'ada@example.test','password':b.PASSWORD})[0]==200
assert b.http('POST','/requests/'+p['split']['requests'][1]['request_id']+'/pay',{},p['tokens']['cy'],'zero-restored')[1]['amount']==0
assert b.http('POST','/_test/reset',b.fixture())[0]==204
assert b.http('GET','/me',token=p['tokens']['ada'])[0]==401
print(json.dumps({'portable_after_source_destroyed':True,'repeated_imports':2,'original_settlement_split_payment_replays':True,'tokens_hash_login_permissions_currency_restored':True,'destination_token_revoked':True,'zero_share_pending_request_payable':True,'reset_revokes_imported_tokens':True,'concurrent_logins':50,'max_source_request_seconds':p['max_source_request_seconds'],'max_destination_request_seconds':max(x['seconds'] for x in b.REQUEST_TIMES)}))
'''
    summary = json.loads(run(['docker','exec','-i',DEST,'python','-c',accept_dest],'destination-checks',package))
    summary['revision']=REVISION
    (OUT/'report.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))
finally:
    # The source normally no longer exists; preserve cleanup status without asserting it.
    for name in (SOURCE,DEST):
        subprocess.run(['docker','rm','-f',name],capture_output=True,env=ENV)
