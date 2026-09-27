"""Additional independent S2 concurrency, import and money-range checks."""
import argparse,concurrent.futures,copy,json,time,unittest
from pathlib import Path
import stage1_acceptance as base
from stage2_api import Stage2

class CrossFeature(unittest.TestCase):
    setUp=Stage2.setUp;call=Stage2.call;good=Stage2.good;error=Stage2.error
    authorize=Stage2.authorize;capture=Stage2.capture;hold=Stage2.hold;money=Stage2.money

    def test_invalid_excess_held_import_is_atomic(self):
        aid=self.authorize(80)['authorization_id']
        before=self.good('GET','/_test/export',who=None,status=200)
        bad=copy.deepcopy(before)
        bad['state']['authorizations'][aid]['amount']=101
        bad['state']['authorizations'][aid]['remaining_amount']=101
        bad['state']['authorization_lifecycle'][aid][0]['remaining_amount']=101
        self.error(422,'validation_failed','POST','/_test/import',bad,who=None)
        self.assertEqual(self.good('GET','/_test/export',who=None,status=200),before)
        self.assertEqual(self.money()['ada']['available'],20)

    def test_fifty_mixed_lifecycle_spend_and_atomic_snapshots(self):
        aid=self.authorize(80)['authorization_id']
        def work(i):
            if i%6==0:return self.call('POST','/authorizations/'+aid+'/capture',{'amount':5,'final':False},who='bob',key='cap'+str(i))
            if i%6==1:return self.call('POST','/authorizations/'+aid+'/void',{})
            if i%6==2:return self.call('POST','/payments',{'to_handle':'cy','amount':7},key='pay'+str(i))
            if i%6==3:return self.call('POST','/authorizations',{'to_handle':'cy','amount':7},key='hold'+str(i))
            if i%6==4:return self.call('POST','/settlements',{'transfers':[{'from_handle':'ada','to_handle':'cy','amount':7},{'from_handle':'bob','to_handle':'ada','amount':2}]},who='op',key='net'+str(i))
            return self.call('GET','/_test/export',who=None)
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results=list(pool.map(work,range(50)))
        for status,value in results:
            self.assertIn(status,(200,201,409))
            if status==409:self.assertIn(value['error']['code'],('authorization_not_open','insufficient_funds'))
            if 'state' in value:
                state=value['state'];held={uid:0 for uid in state['users']}
                for auth in state['authorizations'].values():
                    if auth['status']=='open':held[auth['from_user_id']]+=auth['remaining_amount']
                self.assertEqual(sum(u['balance'] for u in state['users'].values()),300)
                for uid,user in state['users'].items():self.assertGreaterEqual(user['balance']-held[uid],0)
        self.money()
        auth=self.hold(aid)
        self.assertLessEqual(auth['captured_amount'],80)
        self.assertEqual(auth['remaining_amount'],0)

    def test_expired_seed_import_releases_without_regenerating_receipts(self):
        aid=self.authorize(70)['authorization_id']
        cap=self.capture(aid,20,False,key='original')
        snapshot=self.good('GET','/_test/export',who=None,status=200)
        self.assertEqual(base.http('POST','/_test/import',snapshot)[0],204)
        self.assertEqual(self.good('POST','/authorizations/'+aid+'/capture',{'amount':20,'final':False},who='bob',key='original',status=200),cap)
        self.assertEqual(self.good('GET','/_test/export',who=None,status=200),snapshot)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--revision',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();base.BASE=a.url;out=Path(a.out);out.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    with (out/'tests.log').open('w') as stream:r=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CrossFeature))
    report={'revision':a.revision,'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'seconds':time.monotonic()-started,'requests':len(base.REQUEST_TIMES),'max_request_seconds':max(x['seconds'] for x in base.REQUEST_TIMES)}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));raise SystemExit(0 if r.wasSuccessful() else 1)
