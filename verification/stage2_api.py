"""Independent stage-2 authorization/available-money HTTP acceptance.

Uses stage1_acceptance's transport and fresh-output runner, not product code.
"""
import concurrent.futures
import argparse
import copy
import datetime
import json
from pathlib import Path
import time
import unittest
import uuid
import stage1_acceptance as base


def stamp(delta):
    return (datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=delta)).isoformat()


class Stage2(unittest.TestCase):
    setUp = base.Stage1.setUp
    call = base.Stage1.call
    good = base.Stage1.good
    error = base.Stage1.error
    payment = base.Stage1.payment
    request = base.Stage1.request

    def authorize(self, amount=80, who='ada', target='bob', key=None, **fields):
        return self.good('POST','/authorizations',{'to_handle':target,'amount':amount,**fields},who=who,key=key or uuid.uuid4().hex)

    def hold(self, aid, who='ada'):
        return next(a for a in self.good('GET','/authorizations',who=who,status=200)['authorizations'] if a['authorization_id']==aid)

    def capture(self, aid, amount=None, final=None, who='bob', key=None):
        body={}
        if amount is not None:
            body['amount']=amount
        if final is not None:
            body['final']=final
        return self.good('POST','/authorizations/'+aid+'/capture',body,who=who,key=key or uuid.uuid4().hex)

    def money(self):
        values={h:self.good('GET','/me',who=h,status=200) for h in self.tokens}
        self.assertEqual(sum(v['total'] for v in values.values()),300)
        for v in values.values():
            self.assertEqual(v['balance'],v['total'])
            self.assertEqual(v['available'],v['total']-v['held'])
            self.assertGreaterEqual(v['available'],0)
            self.assertGreaterEqual(v['held'],0)
        return values

    def test_authorization_defaults_and_no_money_movement(self):
        auth=self.authorize()
        self.assertEqual((auth['captured_amount'],auth['remaining_amount'],auth['status'],auth['payment_id'],auth['payment_ids']),(0,80,'open',None,[]))
        self.assertEqual((auth['note'],auth['visibility']),('','public'))
        created=datetime.datetime.fromisoformat(auth['created_at'].replace('Z','+00:00'))
        expires=datetime.datetime.fromisoformat(auth['expires_at'].replace('Z','+00:00'))
        self.assertEqual((expires-created).total_seconds(),600)
        self.assertEqual(self.good('GET','/activity',status=200)['payments'],[])
        me=self.money()['ada']
        self.assertEqual((me['total'],me['held'],me['available']),(100,80,20))
        direct=self.payment(1)
        self.assertIsNone(direct['authorization_id'])

    def test_held_funds_block_payment_request_and_net_settlement(self):
        self.authorize(90)
        self.error(409,'insufficient_funds','POST','/payments',{'to_handle':'bob','amount':11},key='blocked-pay')
        rid=self.request(11)['request_id']
        self.error(409,'insufficient_funds','POST','/requests/'+rid+'/pay',{},key='blocked-request')
        self.error(409,'insufficient_funds','POST','/authorizations',{'to_handle':'bob','amount':11},key='blocked-hold')
        body={'transfers':[{'from_handle':'ada','to_handle':'cy','amount':20},{'from_handle':'bob','to_handle':'ada','amount':9}]}
        self.error(409,'insufficient_funds','POST','/settlements',body,who='op',key='blocked-settlement')
        body['transfers'][1]['amount']=10
        self.good('POST','/settlements',body,who='op',key='blocked-settlement')
        self.assertEqual(self.money()['ada']['available'],0)

    def test_partial_final_capture_release_and_private_receipt(self):
        auth=self.authorize(80,note='  보증금 😀  ',visibility='private')
        p=self.capture(auth['authorization_id'],30)
        self.assertEqual((p['authorization_id'],p['request_id'],p['amount'],p['note'],p['visibility']),(auth['authorization_id'],None,30,'  보증금 😀  ','private'))
        current=self.hold(auth['authorization_id'])
        self.assertEqual((current['status'],current['captured_amount'],current['remaining_amount'],current['payment_id'],current['payment_ids']),('captured',30,0,p['payment_id'],[p['payment_id']]))
        me=self.money()['ada']
        self.assertEqual((me['total'],me['held'],me['available']),(70,0,70))
        self.assertEqual(self.good('GET','/activity',who='cy',status=200)['payments'],[])
        self.error(409,'authorization_not_open','POST','/authorizations/'+auth['authorization_id']+'/capture',{},who='bob',key='closed')

    def test_multiple_nonfinal_captures_order_and_remaining_default(self):
        auth=self.authorize(80)
        aid=auth['authorization_id']
        first=self.capture(aid,21,False)
        second=self.capture(aid,9,False)
        current=self.hold(aid)
        self.assertEqual((current['status'],current['captured_amount'],current['remaining_amount']),('open',30,50))
        self.assertEqual(current['payment_ids'],[first['payment_id'],second['payment_id']])
        self.assertEqual(current['payment_id'],second['payment_id'])
        self.assertEqual(self.money()['ada']['available'],20)
        self.error(422,'capture_exceeds_authorization','POST','/authorizations/'+aid+'/capture',{'amount':51},who='bob',key='too-much')
        last=self.capture(aid,final=False)
        self.assertEqual(last['amount'],50)
        current=self.hold(aid)
        self.assertEqual((current['status'],current['captured_amount'],current['remaining_amount']),('captured',80,0))
        self.assertEqual(current['payment_ids'],[first['payment_id'],second['payment_id'],last['payment_id']])
        self.assertEqual(self.money()['ada']['total'],20)

    def test_void_partial_capture_permissions_and_repeat(self):
        aid=self.authorize()['authorization_id']
        paid=self.capture(aid,20,False)
        for who in ('bob','cy','op'):
            self.error(403,'forbidden','POST','/authorizations/'+aid+'/void',{},who=who)
        current=self.good('POST','/authorizations/'+aid+'/void',{},status=200)
        again=self.good('POST','/authorizations/'+aid+'/void',{},status=200)
        self.assertEqual(current,again)
        self.assertEqual((current['status'],current['captured_amount'],current['remaining_amount']),('voided',20,0))
        self.assertEqual(current['payment_ids'],[paid['payment_id']])
        self.assertEqual(self.money()['ada']['available'],80)

    def test_expiry_releases_partially_captured_remainder(self):
        data=base.fixture()
        data['authorization_ttl_seconds']=1
        self.assertEqual(base.http('POST','/_test/reset',data)[0],204)
        for who in self.tokens:
            self.tokens[who]=base.http('POST','/auth/login',{'email':who+'@example.test','password':base.PASSWORD})[1]['token']
        aid=self.authorize(80)['authorization_id']
        paid=self.capture(aid,20,False)
        time.sleep(1.15)
        me=self.money()['ada']
        self.assertEqual((me['total'],me['held'],me['available']),(80,0,80))
        current=self.hold(aid)
        self.assertEqual((current['status'],current['captured_amount'],current['remaining_amount']),('expired',20,0))
        self.assertEqual(current['payment_ids'],[paid['payment_id']])
        self.assertEqual(self.good('GET','/authorizations?status=open',status=200)['authorizations'],[])
        self.assertEqual(len(self.good('GET','/authorizations?status=expired',status=200)['authorizations']),1)
        self.error(409,'authorization_expired','POST','/authorizations/'+aid+'/capture',{},who='bob',key='expired')
        self.error(409,'authorization_not_open','POST','/authorizations/'+aid+'/void',{})
        for who in ('ada','cy','op'):
            self.error(403,'forbidden','POST','/authorizations/'+aid+'/capture',{},who=who,key='unauthorized')

    def test_seeded_open_expired_closed_and_atomic_bad_reset(self):
        data=base.fixture()
        data['authorizations']=[{'id':'a_'+str(i),'from_user_id':'u_ada','to_user_id':'u_bob','amount':amount,'note':'seed','visibility':'public','status':status,'expires_at':stamp(seconds)} for i,(amount,status,seconds) in enumerate([(70,'open',7200),(90,'open',-7200),(80,'captured',7200),(80,'voided',7200),(80,'expired',-7200)])]
        self.assertEqual(base.http('POST','/_test/reset',data)[0],204)
        for who in self.tokens:
            self.tokens[who]=base.http('POST','/auth/login',{'email':who+'@example.test','password':base.PASSWORD})[1]['token']
        self.assertEqual(self.money()['ada']['held'],70)
        before=self.good('GET','/_test/export',who=None,status=200)
        bad=copy.deepcopy(data)
        bad['authorizations'][0]['amount']=101
        self.error(422,'validation_failed','POST','/_test/reset',bad,who=None)
        self.assertEqual(self.good('GET','/_test/export',who=None,status=200),before)
        for ttl in (0,-1,1.5):
            self.error(422,'validation_failed','POST','/_test/reset',{**data,'authorization_ttl_seconds':ttl},who=None)
        self.assertEqual(self.money()['ada']['available'],30)

    def test_ttl_exact_large_representable_and_unrepresentable_atomic_rejection(self):
        # About6342 years is representable from the current event date and detects
        # arbitrary day/year caps. No fixed expected expiry or saturation is used.
        ttl=200_000_000_000
        data={**base.fixture(),'authorization_ttl_seconds':ttl}
        self.assertEqual(base.http('POST','/_test/reset',data)[0],204)
        for who in self.tokens:
            self.tokens[who]=base.http('POST','/auth/login',{'email':who+'@example.test','password':base.PASSWORD})[1]['token']
        auth=self.authorize(1)
        created=datetime.datetime.fromisoformat(auth['created_at'].replace('Z','+00:00'))
        expiry=datetime.datetime.fromisoformat(auth['expires_at'].replace('Z','+00:00'))
        self.assertEqual(expiry,created+datetime.timedelta(seconds=ttl))
        before=self.good('GET','/_test/export',who=None,status=200)
        for invalid in (10**12,10**100):
            self.error(422,'validation_failed','POST','/_test/reset',{**base.fixture(),'authorization_ttl_seconds':invalid},who=None)
            self.assertEqual(self.good('GET','/_test/export',who=None,status=200),before)
        for wrong_type in (None,True,False,'600',[],{}):
            self.error(400,'malformed_request','POST','/_test/reset',{**base.fixture(),'authorization_ttl_seconds':wrong_type},who=None)
            self.assertEqual(self.good('GET','/_test/export',who=None,status=200),before)

    def test_create_capture_validation_and_auth(self):
        for amount in [0,-1,1000000001,1.5,True,'1',None]:
            self.error(422,'validation_failed','POST','/authorizations',{'to_handle':'bob','amount':amount},key='invalid')
        for field, values in [('note',[None,3,'a'*201]),('visibility',[None,False,'unknown'])]:
            for value in values:
                self.error(422,'validation_failed','POST','/authorizations',{'to_handle':'bob','amount':1,field:value},key='invalid')
        self.error(422,'self_payment','POST','/authorizations',{'to_handle':'ada','amount':1},key='self')
        self.error(404,'not_found','POST','/authorizations',{'to_handle':'missing','amount':1},key='unknown')
        aid=self.authorize()['authorization_id']
        for path,body in [('/authorizations',{'to_handle':'bob','amount':1}),('/authorizations/'+aid+'/capture',{}),('/authorizations/'+aid+'/void',{})]:
            self.error(401,'unauthenticated','POST',path,body,who=None,key='auth')
        for who in ('ada','cy','op'):
            self.error(403,'forbidden','POST','/authorizations/'+aid+'/capture',{},who=who,key='wrong')
        for action in ('capture','void'):
            self.error(404,'not_found','POST','/authorizations/missing/'+action,{},key='missing')
        for amount in (0,-1,True,'1',None,1.5):
            self.error(422,'validation_failed','POST','/authorizations/'+aid+'/capture',{'amount':amount},who='bob',key='invalid')
        for final in ('false',0,None):
            self.error(400,'malformed_request','POST','/authorizations/'+aid+'/capture',{'final':final},who='bob',key='invalid')

    def test_receipt_replay_after_void_and_later_capture(self):
        body={'to_handle':'bob','amount':80}
        auth=self.good('POST','/authorizations',body,key='author')
        aid=auth['authorization_id']
        capbody={'amount':20,'final':False}
        first=self.good('POST','/authorizations/'+aid+'/capture',capbody,who='bob',key='cap')
        self.capture(aid,10,False)
        self.good('POST','/authorizations/'+aid+'/void',{},status=200)
        self.assertEqual(self.good('POST','/authorizations',body,key='author',status=200),auth)
        self.assertEqual(self.good('POST','/authorizations/'+aid+'/capture',capbody,who='bob',key='cap',status=200),first)
        self.error(409,'idempotency_key_reuse','POST','/authorizations/'+aid+'/capture',{'amount':-1},who='bob',key='cap')
        self.error(409,'idempotency_key_reuse','POST','/authorizations',{**body,'amount':True},key='author')

    def test_capture_omission_equality_and_failed_key_reuse(self):
        aid=self.authorize(50)['authorization_id']
        self.error(422,'capture_exceeds_authorization','POST','/authorizations/'+aid+'/capture',{'amount':51},who='bob',key='cap')
        original=self.good('POST','/authorizations/'+aid+'/capture',{},who='bob',key='cap')
        self.error(409,'idempotency_key_reuse','POST','/authorizations/'+aid+'/capture',{'amount':50},who='bob',key='cap')
        self.assertEqual(self.good('POST','/authorizations/'+aid+'/capture',{},who='bob',key='cap',status=200),original)

    def test_authorization_privacy_directions_status_pagination(self):
        ids=[self.authorize(1)['authorization_id'] for _ in range(3)]
        self.authorize(1,who='bob',target='ada')
        self.assertEqual(self.good('GET','/authorizations',who='op',status=200)['authorizations'],[])
        self.assertEqual(len(self.good('GET','/authorizations?direction=incoming',status=200)['authorizations']),1)
        page=self.good('GET','/authorizations?direction=outgoing&status=open&limit=2',status=200)
        self.assertEqual([a['authorization_id'] for a in page['authorizations']],list(reversed(ids[1:])))
        self.assertTrue(page['has_more'])
        self.assertFalse(self.good('GET','/authorizations?direction=outgoing&limit=2&offset=2',status=200)['has_more'])
        for query in ('status=no','direction=no','limit=0','limit=201','limit=1e2','offset=-1','offset=4.0','limit='):
            self.error(422,'validation_failed','GET','/authorizations?'+query)

    def test_concurrent_authorization_exactly_once_and_overspending(self):
        body={'to_handle':'bob','amount':60}
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            same=list(pool.map(lambda _:self.call('POST','/authorizations',body,key='hold-race'),range(50)))
        self.assertEqual(sum(s==201 for s,_ in same),1)
        self.assertEqual(sum(s==200 for s,_ in same),49)
        self.assertTrue(all(value==same[0][1] for _,value in same))
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results=list(pool.map(lambda i:self.call('POST','/authorizations',{'to_handle':'cy','amount':3},key='distinct'+str(i)),range(50)))
        self.assertEqual(sum(s==201 for s,_ in results),13)
        self.assertTrue(all(s==201 or (s==409 and v['error']['code']=='insufficient_funds') for s,v in results))
        self.assertEqual(self.money()['ada']['available'],1)

    def test_concurrent_capture_exactly_once_and_remaining_funding(self):
        aid=self.authorize(100)['authorization_id']
        path='/authorizations/'+aid+'/capture'
        body={'amount':10,'final':False}
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            same=list(pool.map(lambda _:self.call('POST',path,body,who='bob',key='same'),range(50)))
        self.assertEqual(sum(s==201 for s,_ in same),1)
        self.assertEqual(sum(s==200 for s,_ in same),49)
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results=list(pool.map(lambda i:self.call('POST',path,{'amount':4,'final':False},who='bob',key='capture'+str(i)),range(50)))
        self.assertEqual(sum(s==201 for s,_ in results),22)
        self.assertTrue(all(s==201 or (s==422 and v['error']['code']=='capture_exceeds_authorization') for s,v in results))
        auth=self.hold(aid)
        self.assertEqual((auth['captured_amount'],auth['remaining_amount'],auth['status']),(98,2,'open'))
        self.assertEqual(self.money()['ada']['available'],0)

    def test_capture_vs_void_and_available_payment_competition(self):
        aid=self.authorize(80)['authorization_id']
        actions=[('/authorizations/'+aid+'/capture',{},'bob','cap'),('/authorizations/'+aid+'/void',{},'ada',None)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda a:self.call('POST',a[0],a[1],who=a[2],key=a[3]),actions))
        self.assertEqual(sum(s in (200,201) for s,_ in results),1)
        self.assertTrue(all(s in (200,201,409) for s,_ in results))
        self.money()

    def test_fifty_mixed_payments_holds_and_settlement_debits(self):
        data=base.fixture()
        data['users'][0]['balance']=20
        data['users'][1]['balance']=280
        self.assertEqual(base.http('POST','/_test/reset',data)[0],204)
        for who in self.tokens:
            self.tokens[who]=base.http('POST','/auth/login',{'email':who+'@example.test','password':base.PASSWORD})[1]['token']
        def work(i):
            if i%3==0:
                return self.call('POST','/payments',{'to_handle':'cy','amount':3},key='mixed'+str(i))
            if i%3==1:
                return self.call('POST','/authorizations',{'to_handle':'cy','amount':3},key='mixed'+str(i))
            return self.call('POST','/settlements',{'transfers':[{'from_handle':'ada','to_handle':'cy','amount':3}]},who='op',key='mixed'+str(i))
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results=list(pool.map(work,range(50)))
        self.assertEqual(sum(s==201 for s,_ in results),6)
        self.assertTrue(all(s==201 or (s==409 and r['error']['code']=='insufficient_funds') for s,r in results))
        self.assertEqual(self.money()['ada']['available'],2)

    def test_populated_export_import_preserves_partial_holds_and_replays(self):
        body={'to_handle':'bob','amount':80,'visibility':'private'}
        auth=self.good('POST','/authorizations',body,key='auth')
        aid=auth['authorization_id']
        capbody={'amount':30,'final':False}
        paid=self.good('POST','/authorizations/'+aid+'/capture',capbody,who='bob',key='cap')
        snapshot=self.good('GET','/_test/export',who=None,status=200)
        self.good('POST','/authorizations/'+aid+'/void',{},status=200)
        self.assertEqual(base.http('POST','/_test/import',snapshot)[0],204)
        current=self.hold(aid)
        self.assertEqual((current['status'],current['captured_amount'],current['remaining_amount']),('open',30,50))
        self.assertEqual(self.money()['ada']['held'],50)
        self.assertEqual(self.good('POST','/authorizations',body,key='auth',status=200),auth)
        self.assertEqual(self.good('POST','/authorizations/'+aid+'/capture',capbody,who='bob',key='cap',status=200),paid)
        self.capture(aid,final=False)
        self.assertEqual(self.money()['ada']['total'],20)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    base.BASE=args.url.rstrip('/')
    out=Path(args.out)
    out.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    with (out/'tests.log').open('w') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Stage2))
    report={'suite':'independent-stage2-api','revision':args.revision,'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),'seconds':time.monotonic()-started,'request_count':len(base.REQUEST_TIMES),'max_request_seconds':max((x['seconds'] for x in base.REQUEST_TIMES),default=0),'failed_tests':[t.id() for t,_ in result.failures+result.errors]}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    raise SystemExit(0 if result.wasSuccessful() and result.testsRun and not result.skipped else 1)
