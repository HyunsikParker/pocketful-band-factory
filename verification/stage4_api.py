"""Independent spec-derived Stage4 refund/batch HTTP tests, no product imports.

Synthetic disposable state only; export assertions use boolean equality to avoid
printing credentials. Derived before reading Stage4 implementation or shipped tests.
"""
import argparse,concurrent.futures,copy,json,time,unittest,uuid
from pathlib import Path
import stage1_acceptance as base
import stage3_api as temporal

T0,T1,T2,T3,T4=temporal.T0,temporal.T1,temporal.T2,temporal.T3,temporal.T4

class Stage4(unittest.TestCase):
    call=base.Stage1.call;good=base.Stage1.good;error=base.Stage1.error
    payment=base.Stage1.payment;request=base.Stage1.request
    reset=temporal.Stage3.reset;me=temporal.Stage3.me;statement=temporal.Stage3.statement
    revisions=temporal.Stage3.revisions;correction=temporal.Stage3.correction
    snapshot=temporal.Stage3.snapshot;authorize=temporal.Stage3.authorize
    capture=temporal.Stage3.capture;hold=temporal.Stage3.hold

    def setUp(self):self.reset(temporal.history_fixture())
    def refund(self,pid='p_z',amount=20,who='bob',key=None):
        return self.good('POST','/payments/'+pid+'/refunds',{'amount':amount},who=who,key=key or uuid.uuid4().hex)
    def item(self,pid='p_z',amount=100,effective=T1,expected=1,**extra):
        return {'payment_id':pid,'expected_revision':expected,'amount':amount,'effective_at':effective,'reason':'batch 정정 😀',**extra}
    def batch(self,items=None,key=None,who='op',status=201):
        return self.good('POST','/correction-batches',{'corrections':items or [self.item()]},who=who,key=key or uuid.uuid4().hex,status=status)
    def unchanged(self,before):self.assertTrue(self.snapshot()==before,'failed operation changed exported state')
    def reject(self,status,code,path,body,who='op',key='failed'):
        before=self.snapshot();self.error(status,code,'POST',path,body,who=who,key=key);self.unchanged(before)
    def money(self):
        rows={h:self.me(who=h) for h in self.tokens}
        self.assertTrue(all(r['available']>=0 and r['total']==r['balance'] and r['available']+r['held']==r['total'] for r in rows.values()))
        return {h:r['balance'] for h,r in rows.items()}
    def cycle(self):
        d=base.fixture()
        for u in d['users']:u['balance']=0
        d['payments']=[{'id':'cycle_a','from_user_id':'u_ada','to_user_id':'u_bob','amount':100,'created_at':T1},
                       {'id':'cycle_b','from_user_id':'u_bob','to_user_id':'u_ada','amount':100,'created_at':T1}]
        self.reset(d)
    def settlement(self):
        return self.good('POST','/settlements',{'transfers':[{'from_handle':'ada','to_handle':'bob','amount':20},{'from_handle':'bob','to_handle':'cy','amount':10}]},who='op',key=uuid.uuid4().hex)
    def parallel(self,fn,n=50):
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:return list(pool.map(fn,range(n)))

    def test_refund_receipt_direction_verbatim_visibility_and_original(self):
        p=self.payment(60,note='  한글\n😀 <script>  ',visibility='private',key='original')
        before=self.money();r=self.refund(p['payment_id'],20)
        self.assertEqual((r['from_user_id'],r['to_user_id'],r['amount']),('u_bob','u_ada',20))
        for k in ('note','visibility'):self.assertEqual(r[k],p[k])
        self.assertEqual(r['refund_of'],p['payment_id'])
        for k in ('request_id','authorization_id','settlement_id'):self.assertIsNone(r[k])
        self.assertIsNone(p['refund_of']);self.assertEqual(self.me()['balance'],before['ada']+20)
        self.assertEqual(sum(self.money().values()),3000)
        feed=self.good('GET','/activity',who='cy',status=200)['payments']
        self.assertNotIn(r['payment_id'],[x['payment_id'] for x in feed])
        self.assertEqual(self.good('POST','/payments',{'to_handle':'bob','amount':60,'note':p['note'],'visibility':'private'},key='original',status=200),p)

    def test_refund_validation_auth_and_unknown_atomic(self):
        path='/payments/p_z/refunds'
        self.reject(401,'unauthenticated',path,{'amount':1},who=None)
        for who in ('ada','cy','op'):self.reject(403,'forbidden',path,{'amount':1},who=who)
        self.reject(404,'not_found','/payments/absent/refunds',{'amount':1},who='bob')
        for amount in (None,True,'1',0,-1,1.5,1000000001):self.reject(422,'validation_failed',path,{'amount':amount},who='bob')
        self.reject(422,'validation_failed',path,{},who='bob')

    def test_refund_cumulative_current_corrected_cap(self):
        self.correction(amount=120)
        self.refund(amount=70);self.refund(amount=50)
        self.reject(422,'refund_exceeds_payment','/payments/p_z/refunds',{'amount':1},who='bob')
        self.correction(amount=150,expected=2);self.refund(amount=30)
        self.reject(422,'refund_exceeds_payment','/payments/p_z/refunds',{'amount':1},who='bob')

    def test_refund_available_hold_rejection_and_failed_key_reuse(self):
        self.authorize(amount=1140,who='bob',target='cy')
        self.reject(409,'insufficient_funds','/payments/p_z/refunds',{'amount':20},who='bob',key='retry')
        self.payment(20,target='bob');self.refund(amount=20,key='retry')
        self.assertEqual(self.me(who='bob')['held'],1140)

    def test_refund_request_paid_remains_paid(self):
        q=self.request(amount=30);p=self.good('POST','/requests/'+q['request_id']+'/pay',{},key='pay')
        self.refund(p['payment_id'],30)
        rows=self.good('GET','/requests',status=200)['requests'];row=next(x for x in rows if x['request_id']==q['request_id'])
        self.assertEqual(row['status'],'paid');self.assertEqual(row['payment_id'],p['payment_id'])
        self.assertEqual(self.good('POST','/requests/'+q['request_id']+'/pay',{},key='pay',status=200),p)

    def test_refund_partial_and_final_capture_do_not_restore_hold(self):
        h=self.authorize(100);p=self.capture(h['authorization_id'],30,False);self.refund(p['payment_id'],20)
        state=self.hold(h['authorization_id']);self.assertEqual((state['status'],state['remaining_amount'],state['captured_amount']),('open',70,30))
        final=self.capture(h['authorization_id'],20,True);self.refund(final['payment_id'],20)
        state=self.hold(h['authorization_id']);self.assertEqual((state['status'],state['remaining_amount'],state['captured_amount']),('captured',0,50))
        self.assertEqual(self.me()['held'],0)

    def test_refund_settlement_keeps_membership_and_original_receipt(self):
        s=self.settlement();p=s['payments'][0];self.refund(p['payment_id'],20)
        originals={r['payment_id']:r for r in self.good('GET','/activity',status=200)['payments']}
        self.assertEqual(originals[p['payment_id']],p)
        self.reject(422,'incomplete_settlement','/correction-batches',{'corrections':[self.item(p['payment_id'],20,p['created_at'])]})

    def test_refund_of_refund_and_immutable_linked_corrections(self):
        r=self.refund();rid=r['payment_id']
        self.reject(422,'invalid_refund_target','/payments/'+rid+'/refunds',{'amount':1},who='ada')
        body=self.item(rid,10,r['created_at']);body.pop('payment_id')
        self.reject(422,'linked_payment_immutable','/payments/'+rid+'/corrections',body,who='bob')
        self.reject(422,'linked_payment_immutable','/correction-batches',{'corrections':[self.item(rid,10,r['created_at'])]})

    def test_correction_and_batch_cannot_reduce_below_refunded(self):
        self.refund(amount=40)
        body=self.item(amount=39);body.pop('payment_id')
        self.reject(422,'refund_exceeds_payment','/payments/p_z/corrections',body,who='ada')
        self.reject(422,'refund_exceeds_payment','/correction-batches',{'corrections':[self.item(amount=39)]})
        r=self.correction(amount=40);self.assertEqual(r['amount'],40)
        self.batch([self.item(amount=40,expected=2)])

    def test_refund_replay_after_later_correction_and_zero_remaining(self):
        r=self.refund(amount=200,key='same');self.correction(amount=200)
        self.assertEqual(self.good('POST','/payments/p_z/refunds',{'amount':200},who='bob',key='same',status=200),r)
        self.reject(409,'idempotency_key_reuse','/payments/p_z/refunds',{'amount':False},who='bob',key='same')

    def test_refund_history_known_time_and_frozen_snapshot(self):
        old=self.statement();r=self.refund(amount=25)
        self.assertEqual(self.statement(snapshot=old['snapshot']),old)
        oldview=self.me(as_of=T3);self.assertEqual(oldview['balance'],820)
        now=self.me(as_of=r['created_at']);self.assertEqual(now['balance'],845)
        self.assertEqual(self.me(as_of=temporal.nowshift(3600),known_at=T3)['balance'],820)
        entries=self.statement()['entries'];entry=next(x for x in entries if x['payment']['payment_id']==r['payment_id'])
        self.assertEqual((entry['delta'],entry['revision'],entry['effective_at'],entry['recorded_at']),(25,1,r['created_at'],r['created_at']))

    def test_refund_fifty_identical_key_exactly_once(self):
        body={'amount':17};rows=self.parallel(lambda i:self.call('POST','/payments/p_z/refunds',body,who='bob',key='race'))
        self.assertEqual([x[0] for x in rows].count(201),1);self.assertEqual([x[0] for x in rows].count(200),49)
        self.assertTrue(all(x[1]==rows[0][1] for x in rows));self.assertEqual(self.me()['balance'],837)

    def test_refund_fifty_distinct_keys_never_exceed_cap(self):
        rows=self.parallel(lambda i:self.call('POST','/payments/p_z/refunds',{'amount':10},who='bob',key='r'+str(i)))
        self.assertEqual([x[0] for x in rows].count(201),20)
        self.assertTrue(all(s==201 or (s==422 and r['error']['code']=='refund_exceeds_payment') for s,r in rows))
        self.assertEqual(sum(self.money().values()),3000)

    def test_batch_auth_operator_does_not_gain_revision_privacy(self):
        body={'corrections':[self.item()]}
        self.reject(401,'unauthenticated','/correction-batches',body,who=None)
        self.reject(403,'forbidden','/correction-batches',body,who='ada')
        self.error(404,'not_found','GET','/payments/p_z/revisions',who='op')
        self.batch();self.error(404,'not_found','GET','/payments/p_z/revisions',who='op')

    def test_batch_shapes_distinctness_one_and_32_and_33(self):
        for value in (None,False,{},'',[],[None],[self.item(),self.item()]):
            self.reject(422,'validation_failed','/correction-batches',{'corrections':value})
        d=base.fixture();d['users'][0]['balance']=1000;self.reset(d)
        ps=[self.payment(1)['payment_id'] for _ in range(32)]
        rows=[self.item(p,0,effective=temporal.nowshift(-3600)) for p in ps]
        # Each original debit is removed, so earlier zero revisions cannot overdraw.
        self.reject(422,'validation_failed','/correction-batches',{'corrections':rows+[self.item('absent')]})
        result=self.batch(rows);self.assertEqual(len(result['revisions']),32)

    def test_batch_ordinary_validation_and_unknown_fields(self):
        original=self.item()
        for field in ('expected_revision','amount','effective_at','reason'):
            row=original.copy();del row[field];self.reject(422,'validation_failed','/correction-batches',{'corrections':[row]})
        for field,values in [('expected_revision',[0,True,'1',1.5]),('amount',[-1,1000000001,True,'1',.5]),('reason',['','x'*201,None]),('effective_at',['',T1[:-6],temporal.nowshift(3600)])]:
            for value in values:self.reject(422,'validation_failed','/correction-batches',{'corrections':[{**original,field:value}]})
        self.batch([{**original,'ignored':{'x':True}}])

    def test_batch_order_shared_monotonic_recording_and_originals(self):
        old=self.statement();body=[self.item('p_m',20,T2),self.item('p_z',100,T1)]
        result=self.batch(body,key='order')
        self.assertEqual([r['payment_id'] for r in result['revisions']],['p_m','p_z'])
        self.assertTrue(all(r['recorded_at']==result['recorded_at'] and r['correction_batch_id']==result['correction_batch_id'] for r in result['revisions']))
        again=self.batch([self.item('p_m',21,T2,2),self.item('p_z',101,T1,2)])
        self.assertGreater(temporal.instant(again['recorded_at']),temporal.instant(result['recorded_at']))
        self.assertEqual(self.statement(snapshot=old['snapshot']),old)
        self.assertEqual(self.batch(body,key='order',status=200),result)
        self.reject(409,'idempotency_key_reuse','/correction-batches',{'corrections':[{}]},key='order')
        feed=self.good('GET','/activity',status=200)['payments'];self.assertEqual(next(x for x in feed if x['payment_id']=='p_z')['amount'],200)

    def test_batch_input_order_errors_precede_completeness_and_funds(self):
        s=self.settlement();p=s['payments'][0]
        cases=[([self.item('absent'),self.item(expected=9)],404,'not_found'),
               ([self.item(expected=9),self.item('absent')],409,'stale_revision'),
               ([self.item(p['payment_id'],999999999,p['created_at']),self.item('absent')],404,'not_found'),
               ([self.item(p['payment_id'],999999999,p['created_at'])],422,'incomplete_settlement'),
               ([self.item(amount=999999999),self.item('p_m',-1)],422,'validation_failed')]
        for rows,status,code in cases:self.reject(status,code,'/correction-batches',{'corrections':rows})

    def test_batch_settlement_complete_offset_equivalent_and_membership(self):
        s=self.settlement();a,b=s['payments'];at=a['created_at'];offset=temporal.instant(at).astimezone(temporal.datetime.timezone(temporal.datetime.timedelta(hours=2))).isoformat()
        rows=[self.item(b['payment_id'],5,offset),self.item(a['payment_id'],10,at)]
        out=self.batch(rows);self.assertEqual([r['effective_at'] for r in out['revisions']],[offset,at])
        originals={p['payment_id']:p for p in self.good('GET','/activity',status=200)['payments']}
        self.assertEqual(originals[a['payment_id']],a);self.assertEqual(originals[b['payment_id']],b)
        body=self.item(a['payment_id'],10,at,2);body.pop('payment_id')
        self.reject(422,'linked_payment_immutable','/payments/'+a['payment_id']+'/corrections',body,who='ada')

    def test_batch_settlement_different_instants_rejected(self):
        s=self.settlement();a,b=s['payments']
        self.reject(422,'validation_failed','/correction-batches',{'corrections':[self.item(a['payment_id'],20,T1),self.item(b['payment_id'],10,T2)]})

    def test_batch_settlement_submicrosecond_instants_remain_distinct(self):
        s=self.settlement();a,b=s['payments']
        first='2020-01-01T01:00:00.0000001Z';later='2020-01-01T01:00:00.0000002Z'
        self.reject(422,'validation_failed','/correction-batches',{'corrections':[self.item(a['payment_id'],20,first),self.item(b['payment_id'],10,later)]})
        self.batch([self.item(a['payment_id'],20,first),self.item(b['payment_id'],10,'2020-01-01T03:00:00.000000100+02:00')])

    def test_batch_refunded_settlement_item_error_before_completeness(self):
        s=self.settlement();a,b=s['payments'];self.refund(a['payment_id'],10)
        self.reject(422,'refund_exceeds_payment','/correction-batches',{'corrections':[self.item(a['payment_id'],9,a['created_at'])]})
        self.batch([self.item(a['payment_id'],10,a['created_at']),self.item(b['payment_id'],5,b['created_at'])])

    def test_batch_shared_known_boundary_selects_all_revisions(self):
        r=self.batch([self.item('p_z',100,T1),self.item('p_m',20,T2)])
        self.assertEqual(self.me(as_of=T3,known_at=T3)['balance'],820)
        self.assertEqual(self.me(as_of=T3,known_at=r['recorded_at'])['balance'],930)
        rows=self.statement(known_at=r['recorded_at'])['entries']
        self.assertEqual([e['revision'] for e in rows if e['payment']['payment_id'] in ('p_z','p_m')],[2,2])
        self.assertEqual(sum(self.me(who=h,as_of=T3,known_at=r['recorded_at'])['balance'] for h in self.tokens),3000)

    def test_batch_collective_affordability_not_gross(self):
        self.cycle();r=self.batch([self.item('cycle_a',1000),self.item('cycle_b',1000)])
        self.assertEqual(len(r['revisions']),2);self.assertEqual(self.money(),{'ada':0,'bob':0,'cy':0,'op':0})

    def test_batch_collective_available_holds_and_failure_rollback(self):
        self.authorize(810)
        self.reject(409,'insufficient_funds','/correction-batches',{'corrections':[self.item(amount=250)]})
        # Zero net correction with a matching opposite movement still preserves holds.
        self.batch([self.item(amount=210),self.item('p_a',60,T2)])
        self.assertEqual(self.me()['held'],810)

    def test_batch_current_precedes_historical_and_combined_history(self):
        self.reset(temporal.Stage3.funding_fixture(self))
        rows=[self.item('p_aout',101,T1)]
        self.reject(409,'insufficient_funds','/correction-batches',{'corrections':rows})
        self.reject(409,'historical_overdraft','/correction-batches',{'corrections':[self.item('p_aout',100,T1)]})
        out=self.batch([self.item('p_zfund',100,T1),self.item('p_aout',100,T1)])
        self.assertEqual(len(out['revisions']),2);self.assertEqual(self.me(as_of=T1)['balance'],0)

    def test_batch_historical_held_boundary_after_void_still_matters(self):
        h=self.authorize(1100,who='bob',target='cy');self.good('POST','/authorizations/'+h['authorization_id']+'/void',{},who='bob',status=200)
        self.reject(409,'historical_overdraft','/correction-batches',{'corrections':[self.item(amount=0)]})
        self.batch([self.item(amount=150)])

    def test_batch_captures_and_refunds_item_immutable(self):
        h=self.authorize();p=self.capture(h['authorization_id'],10,True);r=self.refund()
        for row in (self.item(p['payment_id'],0,p['created_at']),self.item(r['payment_id'],0,r['created_at'])):
            self.reject(422,'linked_payment_immutable','/correction-batches',{'corrections':[row]})

    def test_batch_request_payment_correctable_without_reopening(self):
        q=self.request();p=self.good('POST','/requests/'+q['request_id']+'/pay',{},key='reqpay')
        self.batch([self.item(p['payment_id'],0,p['created_at'])])
        self.assertEqual(self.good('POST','/requests/'+q['request_id']+'/pay',{},key='reqpay',status=200),p)
        self.assertEqual(next(x for x in self.good('GET','/requests',status=200)['requests'] if x['request_id']==q['request_id'])['status'],'paid')

    def test_batch_fifty_identical_key_replay(self):
        body={'corrections':[self.item()]};rows=self.parallel(lambda i:self.call('POST','/correction-batches',body,who='op',key='same'))
        self.assertEqual([s for s,_ in rows].count(201),1);self.assertEqual([s for s,_ in rows].count(200),49)
        self.assertTrue(all(r==rows[0][1] for _,r in rows));self.assertEqual(len(self.revisions()),2)

    def test_overlapping_batches_cannot_both_succeed(self):
        a={'corrections':[self.item(),self.item('p_m',20,T2)]};b={'corrections':[self.item(),self.item('p_a',40,T2)]}
        rows=self.parallel(lambda i:self.call('POST','/correction-batches',a if i%2 else b,who='op',key='cas'+str(i)))
        self.assertEqual([s for s,_ in rows].count(201),1)
        self.assertTrue(all(s==201 or (s==409 and r['error']['code']=='stale_revision') for s,r in rows));self.assertEqual(len(self.revisions()),2)

    def test_single_correction_races_batch_cas(self):
        item=self.item();single={k:v for k,v in item.items() if k!='payment_id'}
        def call(i):return self.call('POST','/correction-batches' if i%2 else '/payments/p_z/corrections',{'corrections':[item]} if i%2 else single,who='op' if i%2 else 'ada',key='mix'+str(i))
        rows=self.parallel(call);self.assertEqual([s for s,_ in rows].count(201),1)
        self.assertTrue(all(s==201 or (s==409 and r['error']['code']=='stale_revision') for s,r in rows))

    def test_refund_races_reduction_never_breaches_cap(self):
        # Either refund150 wins and reduction100 fails cap, or reduction wins and refund fails cap.
        def call(i):
            if i==0:return self.call('POST','/payments/p_z/refunds',{'amount':150},who='bob',key='refund')
            row=self.item(amount=100);row.pop('payment_id');return self.call('POST','/payments/p_z/corrections',row,key='correct')
        rows=self.parallel(call,2);self.assertEqual([s for s,_ in rows].count(201),1)
        self.assertEqual([r['error']['code'] for s,r in rows if s!=201],['refund_exceeds_payment']);self.money()

    def test_mixed_refund_hold_capture_void_spend_settlement_reads(self):
        h=self.authorize(20,who='bob',target='cy');aid=h['authorization_id']
        def call(i):
            key='mixed'+str(i);kind=i%7
            if kind==0:return self.call('POST','/payments/p_z/refunds',{'amount':1},who='bob',key=key)
            if kind==1:return self.call('POST','/authorizations',{'to_handle':'ada','amount':2},who='bob',key=key)
            if kind==2:return self.call('POST','/authorizations/'+aid+'/capture',{'amount':1,'final':False},who='cy',key=key)
            if kind==3:return self.call('POST','/authorizations/'+aid+'/void',{},who='bob')
            if kind==4:return self.call('POST','/payments',{'to_handle':'ada','amount':1},who='bob',key=key)
            if kind==5:return self.call('POST','/settlements',{'transfers':[{'from_handle':'bob','to_handle':'ada','amount':2},{'from_handle':'ada','to_handle':'bob','amount':1}]},who='op',key=key)
            return self.call('GET','/me',who='bob')
        rows=self.parallel(call)
        self.assertTrue(all(s in (200,201) or (s==409 and r['error']['code']=='authorization_not_open') for s,r in rows))
        for s,r in rows:
            if 'available' in r:self.assertGreaterEqual(r['available'],0);self.assertEqual(r['available']+r['held'],r['total'])
        self.assertEqual(sum(self.money().values()),3000)

    def test_new_key_bounds_json_equality_and_path_scope(self):
        for path,body,who in [('/payments/p_z/refunds',{'amount':1},'bob'),('/correction-batches',{'corrections':[self.item()]},'op')]:
            for key in (None,''):self.reject(400,'missing_idempotency_key',path,body,who,key)
            self.reject(422,'validation_failed',path,body,who,'x'*256)
            r=self.good('POST',path,body,who=who,key='x'*255)
            numeric=json.dumps(body).replace('"amount": 1','"amount": 1.0') if who=='bob' else json.dumps(body).replace('"expected_revision": 1','"expected_revision": 1.0')
            s,replay=self.call('POST',path,who=who,key='x'*255,raw=numeric.encode());self.assertEqual((s,replay),(200,r))
        # Same receiver/key on a different payment path is independent.
        p=self.payment(1);self.refund(p['payment_id'],1,key='x'*255)

    def test_all_ten_original_keyed_responses_snapshots_roundtrip(self):
        receipts=[]
        def write(path,body,who,key):
            r=self.good('POST',path,body,who=who,key=key);receipts.append((path,body,who,key,r));return r
        p=write('/payments',{'to_handle':'bob','amount':20},'ada','payment')
        q=write('/requests',{'payer_handle':'ada','amount':10},'bob','request')
        write('/requests/'+q['request_id']+'/pay',{},'ada','pay')
        write('/splits',{'amount':1,'participant_handles':['ada','bob','cy']},'ada','split')
        s=write('/settlements',{'transfers':[{'from_handle':'ada','to_handle':'bob','amount':1}]},'op','settlement')
        h=write('/authorizations',{'to_handle':'bob','amount':20},'ada','hold')
        write('/authorizations/'+h['authorization_id']+'/capture',{'amount':5,'final':False},'bob','capture')
        write('/payments/'+p['payment_id']+'/corrections',{'expected_revision':1,'amount':15,'effective_at':p['created_at'],'reason':'adjust'},'ada','correction')
        write('/payments/'+p['payment_id']+'/refunds',{'amount':5},'bob','refund')
        old=self.statement();write('/correction-batches',{'corrections':[self.item(p['payment_id'],10,p['created_at'],2)]},'op','batch')
        latest=self.statement();export=self.snapshot();tokens=self.tokens.copy();self.reset(base.fixture())
        self.good('POST','/_test/import',export,who=None,status=204)
        self.tokens=tokens
        for path,body,who,key,r in receipts:self.assertEqual(self.good('POST',path,body,who=who,key=key,status=200),r)
        self.assertEqual(self.statement(snapshot=old['snapshot']),old);self.assertEqual(self.statement(snapshot=latest['snapshot']),latest)
        self.refund(p['payment_id'],5);self.reject(422,'refund_exceeds_payment','/payments/'+p['payment_id']+'/refunds',{'amount':1},who='bob')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--revision',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    base.BASE=a.url;out=Path(a.out);out.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    with (out/'tests.log').open('w') as stream:r=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Stage4))
    report={'revision':a.revision,'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'seconds':time.monotonic()-start,'requests':len(base.REQUEST_TIMES),'max_request_seconds':max((x['seconds'] for x in base.REQUEST_TIMES),default=0),'failed_tests':[t.id() for t,_ in r.failures+r.errors]}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));raise SystemExit(0 if r.wasSuccessful() and r.testsRun and not r.skipped else 1)
