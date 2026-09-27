"""Source-destroyed S1/S2/S3->S4 populated migration, private payload only in RAM/stdin.

Images must already correspond to reviewed immutable revisions. Orchestrator never logs
private phase stdout or raw exports. All requests go through actual container HTTP.
"""
import argparse,json,os,subprocess,sys,time,uuid
from pathlib import Path
import stage1_acceptance as api

def prepare(stage):
    api.BASE='http://127.0.0.1:8080';d=api.fixture()
    d['users'][0]['balance']=10000;d['users'][1]['balance']=10000
    assert api.http('POST','/_test/reset',d)[0]==204
    tokens={}
    for h in ('ada','bob','cy','op'):
        s,v=api.http('POST','/auth/login',{'email':h+'@example.test','password':api.PASSWORD});assert s==200;tokens[h]=v['token']
    replays=[]
    def write(path,body,who,key):
        s,r=api.http('POST',path,body,tokens[who],key);assert s==201
        replays.append({'path':path,'body':body,'who':who,'key':key,'response':r});return r
    direct=write('/payments',{'to_handle':'bob','amount':100,'visibility':'private','note':'unchanged 😀'},'ada','lost-direct')
    pending=write('/requests',{'payer_handle':'ada','amount':40},'bob','pending')
    paid=write('/requests',{'payer_handle':'ada','amount':20},'bob','paid-request')
    write('/requests/'+paid['request_id']+'/pay',{'visibility':'private'},'ada','paid-key')
    write('/splits',{'amount':1,'participant_handles':['ada','bob','cy']},'ada','split-zero')
    settlement=write('/settlements',{'transfers':[{'from_handle':'ada','to_handle':'bob','amount':10},{'from_handle':'bob','to_handle':'cy','amount':5}]},'op','settlement')
    holds=[]
    if stage>=2:
        for mode in ('open','partial','final','void'):
            hold=write('/authorizations',{'to_handle':'bob','amount':100,'visibility':'private','note':mode},'ada','authorize-'+mode)
            aid=hold['authorization_id'];holds.append(aid)
            if mode in ('partial','final'):write('/authorizations/'+aid+'/capture',{'amount':30,'final':mode=='final'},'bob','capture-'+mode)
            if mode=='void':assert api.http('POST','/authorizations/'+aid+'/void',{},tokens['ada'])[0]==200
    saved=[]
    if stage==3:
        saved.append(api.http('GET','/statement',token=tokens['ada'])[1])
        write('/payments/'+direct['payment_id']+'/corrections',{'expected_revision':1,'amount':90,'effective_at':direct['created_at'],'reason':'before upgrade'},'ada','source-correction')
        saved.append(api.http('GET','/statement',token=tokens['ada'])[1])
    views={h:api.http('GET','/me',token=t)[1] for h,t in tokens.items()}
    authorizations=api.http('GET','/authorizations',token=tokens['ada'])[1] if stage>=2 else None
    assert api.http('POST','/payments',{'to_handle':'bob','amount':1000000000},tokens['ada'],'failed-key')[0]==409
    s,export=api.http('GET','/_test/export');assert s==200
    return {'stage':stage,'saved':saved,'export':export,'tokens':tokens,'replays':replays,'views':views,'authorizations':authorizations,'holds':holds,'pending':pending,'direct':direct,'settlement':settlement}

def verify(p):
    api.BASE='http://127.0.0.1:8080';tokens=p['tokens']
    assert api.http('POST','/_test/reset',api.fixture('JPY',0))[0]==204
    s,dest=api.http('POST','/auth/signup',{'email':'destination-only@example.test','password':api.PASSWORD,'display_name':'Destination'});assert s==201
    for _ in range(2):
        assert api.http('POST','/_test/import',p['export'])[0]==204
        assert api.http('GET','/me',token=dest['token'])[0]==401
        for h,t in tokens.items():
            s,m=api.http('GET','/me',token=t);assert s==200
            for field in ('user_id','handle','balance','currency','minor_units'):assert m[field]==p['views'][h][field]
            for field in ('total','held','available'):
                if field in p['views'][h]:assert m[field]==p['views'][h][field]
            s,stmt=api.http('GET','/statement',token=t);assert s==200 and stmt['closing_balance']==m['balance']
        for r in p['replays']:
            assert api.http('POST',r['path'],r['body'],tokens[r['who']],r['key'])==(200,r['response'])
        for snapshot in p['saved']:assert api.http('GET','/statement?snapshot='+snapshot['snapshot'],token=tokens['ada'])==(200,snapshot)
        assert api.http('POST','/auth/login',{'email':'ada@example.test','password':api.PASSWORD})[0]==200
    if p['stage']>=2:
        s,holds=api.http('GET','/authorizations',token=tokens['ada']);assert s==200
        current={h['authorization_id']:h for h in holds['authorizations']}
        for old in p['authorizations']['authorizations']:
            for field,value in old.items():assert current[old['authorization_id']][field]==value
        partial=p['holds'][1]
        assert api.http('POST','/authorizations/'+partial+'/capture',{},tokens['bob'],'finish-imported')[0]==201
        assert api.http('POST','/authorizations/'+p['holds'][0]+'/void',{},tokens['ada'])[0]==200
        creation=next(r['response'] for r in p['replays'] if r['key']=='authorize-partial')
        s,historical=api.http('GET','/me?as_of='+creation['created_at'].replace('+','%2B'),token=tokens['ada']);assert s==200 and historical['held']==200
    assert api.http('POST','/requests/'+p['pending']['request_id']+'/pay',{},tokens['ada'],'pay-imported-pending')[0]==201
    assert api.http('POST','/payments',{'to_handle':'bob','amount':1},tokens['ada'],'failed-key')[0]==201
    direct=p['direct'];path='/payments/'+direct['payment_id']
    s,rows=api.http('GET',path+'/revisions',token=tokens['ada']);assert s==200
    assert rows['revisions'][0]['amount']==100 and rows['revisions'][0]['effective_at']==direct['created_at']
    body={'expected_revision':2 if p['stage']==3 else 1,'amount':80,'effective_at':direct['created_at'],'reason':'after upgrade'}
    assert api.http('POST',path+'/corrections',body,tokens['ada'],'after-upgrade')[0]==201
    original=next(r for r in p['replays'] if r['key']=='lost-direct')
    assert api.http('POST',original['path'],original['body'],tokens['ada'],original['key'])==(200,direct)
    member=p['settlement']['payments'][0]
    s,r=api.http('POST','/payments/'+member['payment_id']+'/corrections',body,tokens['ada'],'immutable')
    assert (s,r['error']['code'])==(422,'linked_payment_immutable')
    # A new S3 snapshot and correction retry also survive another replacement import.
    s,snapshot=api.http('GET','/statement',token=tokens['ada']);assert s==200
    s,export=api.http('GET','/_test/export');assert s==200
    assert api.http('POST','/_test/reset',api.fixture())[0]==204
    assert api.http('POST','/_test/import',export)[0]==204
    assert api.http('GET','/statement?snapshot='+snapshot['snapshot'],token=tokens['ada'])==(200,snapshot)
    assert api.http('POST',path+'/corrections',body,tokens['ada'],'after-upgrade')[0]==200
    refund_body={'amount':5};s,refund=api.http('POST',path+'/refunds',refund_body,tokens['bob'],'new-refund');assert s==201
    batch_body={'corrections':[{**body,'payment_id':direct['payment_id'],'expected_revision':body['expected_revision']+1,'amount':75}]}
    s,batch=api.http('POST','/correction-batches',batch_body,tokens['op'],'new-batch');assert s==201
    s,export=api.http('GET','/_test/export');assert s==200
    assert api.http('POST','/_test/import',export)[0]==204
    assert api.http('POST',path+'/refunds',refund_body,tokens['bob'],'new-refund')==(200,refund)
    assert api.http('POST','/correction-batches',batch_body,tokens['op'],'new-batch')==(200,batch)
    return {'source_snapshots_preserved':len(p['saved']),'new_refund_batch_replays':True,'source_stage':p['stage'],'source_destroyed_before_import':True,'repeated_imports':2,'all_original_replays':len(p['replays']),'pending_paid':True,'failed_key_reused':True,'password_login_preserved':True,'tokens_and_permissions_preserved':True,'new_temporal_ledger_correctable':True,'snapshot_and_correction_retry_roundtrip':True,'max_request_seconds':max(x['seconds'] for x in api.REQUEST_TIMES)}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['prepare','verify']);parser.add_argument('--source-stage',type=int,choices=[1,2,3]);parser.add_argument('--source-image');parser.add_argument('--destination-image');parser.add_argument('--source-revision');parser.add_argument('--revision');parser.add_argument('--out')
    args=parser.parse_args()
    if args.phase=='prepare':print(json.dumps(prepare(args.source_stage)));return
    if args.phase=='verify':print(json.dumps(verify(json.load(sys.stdin))));return
    if not all([args.source_stage,args.source_image,args.destination_image,args.source_revision,args.revision,args.out]):parser.error('orchestration requires images, revisions, source stage and output')
    out=Path(args.out);out.mkdir(parents=True,exist_ok=False);here=Path(__file__).parent;runid=uuid.uuid4().hex[:12]
    source='verifier-s4-source-'+runid;dest='verifier-s4-dest-'+runid
    def command(parts,label,data=None,private=False):
        start=time.monotonic();p=subprocess.run(parts,input=data,capture_output=True)
        if not private:(out/(label+'.log')).write_bytes(p.stdout+p.stderr)
        with (out/'operations.jsonl').open('a') as f:f.write(json.dumps({'label':label,'exit':p.returncode,'seconds':time.monotonic()-start})+'\n')
        if p.returncode:raise RuntimeError(label+' failed; private phase output intentionally not logged' if private else label+' failed')
        return p.stdout
    try:
        for name,image in [(source,args.source_image),(dest,args.destination_image)]:
            command(['docker','run','-d','--name',name,'--network','none','--cpus=2','--memory=2g',image],'start-'+name)
            for filename in ('stage1_acceptance.py','stage4_upgrade.py'):command(['docker','cp',str(here/filename),name+':/app/'+filename],'copy-'+name+'-'+filename)
            command(['docker','exec',name,'python','-c',"import urllib.request,time\nfor i in range(100):\n try:\n  assert urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=1).status==200;break\n except Exception:time.sleep(.1)\nelse:raise RuntimeError('not healthy')"],'healthy-'+name)
        payload=command(['docker','exec',source,'python','/app/stage4_upgrade.py','--phase','prepare','--source-stage',str(args.source_stage)],'private-source',private=True)
        command(['docker','rm','-f',source],'destroy-source')
        absent=subprocess.run(['docker','inspect',source],capture_output=True);assert absent.returncode!=0
        result=json.loads(command(['docker','exec','-i',dest,'python','/app/stage4_upgrade.py','--phase','verify'],'destination',payload,private=True))
        result.update({'revision':args.revision,'source_revision':args.source_revision})
        (out/'report.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    finally:
        cleanup={name:subprocess.run(['docker','rm','-f',name],capture_output=True).returncode for name in (source,dest)}
        (out/'cleanup.json').write_text(json.dumps(cleanup)+'\n')

if __name__=='__main__':main()
