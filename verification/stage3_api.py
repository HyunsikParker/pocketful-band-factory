"""Independent Stage3 temporal, correction, snapshot and historical-hold acceptance.

Spec-derived before production review; destructive synthetic fixture HTTP only.
No implementation or shipped tests imported. Oracle is a simple selected-revision sum.
"""
import argparse,concurrent.futures,copy,datetime,json,random,time,unittest,uuid
from pathlib import Path
from urllib.parse import urlencode
import stage1_acceptance as base

UTC=datetime.timezone.utc
T0='2020-01-01T00:00:00+00:00'
T1='2020-01-01T01:00:00+00:00'
T2='2020-01-01T02:00:00+00:00'
T3='2020-01-01T03:00:00+00:00'
T4='2020-01-01T04:00:00+00:00'

def instant(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
def shift(s,seconds):return (instant(s)+datetime.timedelta(seconds=seconds)).isoformat()
def nowshift(seconds):return (datetime.datetime.now(UTC)+datetime.timedelta(seconds=seconds)).isoformat()
def query(path,**values):return path+'?'+urlencode(values)

def history_fixture():
    d=base.fixture()
    for u,b in zip(d['users'],(820,1150,1030,0)):u['balance']=b
    d['payments']=[
        {'id':'p_z','from_user_id':'u_ada','to_user_id':'u_bob','amount':200,'note':'original','visibility':'public','created_at':T1},
        {'id':'p_a','from_user_id':'u_bob','to_user_id':'u_ada','amount':50,'note':'return','visibility':'private','created_at':T2},
        {'id':'p_m','from_user_id':'u_ada','to_user_id':'u_cy','amount':30,'note':'share','visibility':'public','created_at':T2}]
    return d

def selected(revisions,known):
    result=[]
    for pid,rows in revisions.items():
        eligible=[r for r in rows if instant(r['recorded_at'])<=instant(known)]
        if eligible:result.append((pid,eligible[-1]))
    return result

def oracle_balance(uid,opening,parties,revisions,as_of,known):
    amount=opening[uid]
    for pid,r in selected(revisions,known):
        if instant(r['effective_at'])<=instant(as_of):
            sender,receiver=parties[pid]
            amount+=r['amount']*((uid==receiver)-(uid==sender))
    return amount

class Stage3(unittest.TestCase):
    call=base.Stage1.call;good=base.Stage1.good;error=base.Stage1.error
    payment=base.Stage1.payment;request=base.Stage1.request

    def setUp(self):self.reset(history_fixture())

    def reset(self,data):
        self.assertEqual(base.http('POST','/_test/reset',data)[0],204)
        self.tokens={}
        for who in ('ada','bob','cy','op'):
            status,r=base.http('POST','/auth/login',{'email':who+'@example.test','password':base.PASSWORD})
            self.assertEqual(status,200);self.tokens[who]=r['token']

    def me(self,who='ada',**q):return self.good('GET',query('/me',**q),who=who,status=200)
    def statement(self,who='ada',**q):return self.good('GET',query('/statement',**q),who=who,status=200)
    def revisions(self,pid='p_z',who='ada'):return self.good('GET','/payments/'+pid+'/revisions',who=who,status=200)['revisions']
    def correction(self,pid='p_z',amount=100,effective=T1,expected=1,who='ada',key=None,reason='correction'):
        return self.good('POST','/payments/'+pid+'/corrections',{'expected_revision':expected,'amount':amount,'effective_at':effective,'reason':reason},who=who,key=key or uuid.uuid4().hex)
    def snapshot(self):return self.good('GET','/_test/export',who=None,status=200)
    def authorize(self,amount=80,who='ada',target='bob'):
        return self.good('POST','/authorizations',{'to_handle':target,'amount':amount},who=who,key=uuid.uuid4().hex)
    def capture(self,aid,amount,final=False,who='bob'):
        return self.good('POST','/authorizations/'+aid+'/capture',{'amount':amount,'final':final},who=who,key=uuid.uuid4().hex)
    def hold(self,aid,who='ada'):
        return next(x for x in self.good('GET','/authorizations',who=who,status=200)['authorizations'] if x['authorization_id']==aid)
    def assert_money(self,m,total,held):
        self.assertEqual((m['balance'],m['total'],m['held'],m['available']),(total,total,held,total-held))

    def test_seeded_opening_inclusive_balance_and_original_times(self):
        self.assertEqual(self.me()['balance'],820)
        for at,expected in [(T0,1000),(shift(T1,-.000001),1000),(T1,800),(T2,820),(nowshift(3600),820)]:
            m=self.me(as_of=at);self.assertEqual(m['balance'],expected);self.assertEqual(m['as_of'],at)
        r=self.revisions()[0]
        self.assertEqual((r['revision'],r['amount'],r['effective_at'],r['recorded_at'],r['reason']),(1,200,T1,T1,''))

    def test_future_seed_rejected_atomically_and_missing_timestamp_precedes_new(self):
        before=self.snapshot();bad=history_fixture();bad['payments'][0]['created_at']=nowshift(3600)
        self.error(422,'validation_failed','POST','/_test/reset',bad,who=None)
        self.assertEqual(self.snapshot(),before)
        d=history_fixture();del d['payments'][0]['created_at'];self.reset(d)
        seeded=self.revisions()[0];new=self.payment(1)
        self.assertLessEqual(instant(seeded['recorded_at']),instant(new['created_at']))
        self.assertEqual(self.me()['balance'],819)

    def test_temporal_query_validation_offsets_and_exact_echo(self):
        invalid=['','2020-01-01','2020-01-01T01:00:00','nonsense','2020-13-01T00:00:00Z','2020-01-32T00:00:00Z','2020-01-01T25:00:00Z','2020-01-01T00:00:00+25:00']
        for path,fields in [('/me',('as_of','known_at')),('/statement',('from','to','known_at'))]:
            for field in fields:
                for value in invalid:self.error(422,'validation_failed','GET',query(path,**{field:value}))
        offset='2020-01-01T03:00:00+02:00'
        m=self.me(as_of=offset,known_at='2030-01-01T00:00:00Z')
        self.assertEqual(m['balance'],800);self.assertEqual(m['as_of'],offset);self.assertEqual(m['known_at'],'2030-01-01T00:00:00Z')
        self.assertEqual(self.statement(known_at=offset)['known_at'],offset)

    def test_fractional_instants_not_truncated_or_float_rounded(self):
        d=history_fixture();times=['2020-01-01T00:00:00.0000001Z','2020-01-01T00:00:00.0000002Z','2020-01-01T00:00:00.0000003Z']
        for p,t in zip(d['payments'],times):p['created_at']=t
        self.reset(d)
        for t,amount in zip(times,[800,850,820]):self.assertEqual(self.me(as_of=t)['balance'],amount)
        self.assertEqual(self.me(as_of='2020-01-01T00:00:00.000000199999999999999999Z')['balance'],800)
        self.assertEqual(self.me(as_of='2020-01-01T02:00:00.0000002+02:00')['balance'],850)
        self.assertEqual(self.me(known_at=times[0])['balance'],800)
        page=self.statement(**{'from':times[0],'to':times[1]})
        self.assertEqual([e['payment']['payment_id'] for e in page['entries']],['p_z'])

    def test_lowercase_rfc3339_seed_hold_and_import(self):
        d=history_fixture();d['authorizations']=[{'id':'a_lower','from_user_id':'u_ada','to_user_id':'u_bob','amount':200,'status':'open','created_at':'2020-01-01t00:00:00z','expires_at':'2090-01-01t00:00:00.0000001z'}]
        self.reset(d);self.assert_money(self.me(),820,200)
        self.assertEqual(self.hold('a_lower')['status'],'open');export=self.snapshot()
        self.assertEqual(base.http('POST','/_test/import',export)[0],204)
        self.assert_money(self.me(as_of='2020-01-01t01:00:00z'),800,200)
        self.assertEqual(self.hold('a_lower')['status'],'open')

    def test_structural_leap_second_order_and_offset_equivalence(self):
        d=history_fixture();times=['2016-12-31T23:59:59.999999999Z','2016-12-31T23:59:60.1Z','2017-01-01T00:00:00Z']
        for p,t in zip(d['payments'],times):p['created_at']=t
        self.reset(d)
        for t,amount in zip(times,[800,850,820]):self.assertEqual(self.me(as_of=t)['balance'],amount)
        self.assertEqual(self.me(as_of='2017-01-01T01:59:60.1+02:00')['balance'],850)
        self.assertEqual(self.me(as_of='2016-12-31T23:59:60.099999999999Z')['balance'],800)
        for invalid in ['2016-12-30T23:59:60Z','2016-12-31T22:59:60Z','2016-12-31T23:58:60Z']:
            self.error(422,'validation_failed','GET',query('/me',as_of=invalid))

    def test_non_rfc3339_datetime_extensions_rejected(self):
        for value in ['20200101T010000Z','2020-W01-3T01:00:00Z','2020-01-01X01:00:00Z','2020-01-01T01:00:00+0200','2020-01-01T01:00:00+02','2020-01-01T01:00:00+02:00:01','2020-01-01T01:00:00,5Z']:
            self.error(422,'validation_failed','GET',query('/me',as_of=value))

    def test_half_open_window_tie_order_and_full_window_page_balances(self):
        one=self.statement(**{'from':T1,'to':T2})
        self.assertEqual([e['payment']['payment_id'] for e in one['entries']],['p_z'])
        self.assertEqual((one['opening_balance'],one['closing_balance']),(1000,800))
        full=self.statement(**{'from':T2,'to':T3})
        self.assertEqual([e['payment']['payment_id'] for e in full['entries']],['p_a','p_m'])
        self.assertEqual([e['balance_after'] for e in full['entries']],[850,820])
        for offset in (0,1,2,999):
            page=self.statement(**{'from':T2,'to':T3,'limit':1,'offset':offset})
            self.assertEqual(page['entries'],full['entries'][offset:offset+1]);self.assertEqual(page['has_more'],offset+1<len(full['entries']))
            self.assertEqual((page['opening_balance'],page['closing_balance']),(800,820))

    def test_statement_privacy_auth_and_pagination_ranges(self):
        self.assertEqual(self.statement(who='op')['entries'],[])
        self.assertEqual(len(self.good('GET','/activity',who='op',status=200)['payments']),2)
        self.error(401,'unauthenticated','GET','/statement',who=None)
        for key,values in [('limit',['0','201','1e2','4.0','+4','']),('offset',['-1','1e2','4.0','+4',''])]:
            for v in values:self.error(422,'validation_failed','GET',query('/statement',**{key:v}))
        self.assertEqual(self.statement(irrelevant='ignored')['closing_balance'],820)

    def test_correction_money_difference_and_original_receipt_immutability(self):
        original=self.good('GET','/activity',status=200)['payments'];r=self.correction(reason='  정정 😀  ')
        self.assertEqual((r['payment_id'],r['revision'],r['amount'],r['reason']),('p_z',2,100,'  정정 😀  '))
        self.assertGreater(instant(r['recorded_at']),instant(T1))
        self.assertEqual((self.me()['balance'],self.me('bob')['balance']),(920,1050))
        self.assertEqual(self.good('GET','/activity',status=200)['payments'],original)
        self.assertEqual([x['amount'] for x in self.revisions()],[200,100])
        self.assertEqual(self.me(as_of=T0)['balance'],1000)

    def test_zero_revision_is_entry_not_additional_payment(self):
        self.correction(amount=0)
        stmt=self.statement();entry=next(e for e in stmt['entries'] if e['payment']['payment_id']=='p_z')
        self.assertEqual((entry['delta'],entry['payment']['amount'],entry['revision']),(0,0,2))
        self.assertEqual(len(stmt['entries']),3);self.assertEqual(stmt['closing_balance'],1020)
        self.assertEqual(sum(e['delta'] for e in stmt['entries'])+stmt['opening_balance'],stmt['closing_balance'])

    def test_effective_move_and_known_time_select_one_revision(self):
        r=self.correction(amount=100,effective=T3)
        kbefore=shift(r['recorded_at'],-.000001)
        self.assertEqual(self.me(as_of=T1,known_at=kbefore)['balance'],800)
        for t,b in [(T1,1000),(T2,1020),(T3,920)]:self.assertEqual(self.me(as_of=t,known_at=r['recorded_at'])['balance'],b)
        for known,expected in [(T0,1000),(T1,800),(T2,820)]:self.assertEqual(self.me(as_of=T4,known_at=known)['balance'],expected)
        moved=self.statement(known_at=r['recorded_at'])
        self.assertEqual([e['payment']['payment_id'] for e in moved['entries']],['p_a','p_m','p_z'])
        e=moved['entries'][-1];self.assertEqual((e['effective_at'],e['recorded_at'],e['revision']),(T3,r['recorded_at'],2))

    def test_correction_field_rules_and_all_required_fields(self):
        valid={'expected_revision':1,'amount':100,'effective_at':T1,'reason':'valid'}
        for field in valid:
            b=dict(valid);del b[field];self.error(422,'validation_failed','POST','/payments/p_z/corrections',b,key='invalid')
        invalid={'expected_revision':[0,-1,True,'1',None,1.5], 'amount':[-1,1000000001,True,'1',None,1.5], 'reason':['','x'*201,1,None,False], 'effective_at':['',T1[:-6],nowshift(3600),None,123]}
        for field,values in invalid.items():
            for value in values:self.error(422,'validation_failed','POST','/payments/p_z/corrections',{**valid,field:value},key='invalid')
        self.correction(key='invalid',reason='x'*200)

    def test_correction_and_revision_authorization(self):
        b={'expected_revision':1,'amount':100,'effective_at':T1,'reason':'x'}
        self.error(401,'unauthenticated','POST','/payments/p_z/corrections',b,who=None,key='auth')
        self.error(401,'unauthenticated','GET','/payments/p_z/revisions',who=None)
        for who in ('bob','cy','op'):self.error(403,'forbidden','POST','/payments/p_z/corrections',b,who=who,key='auth')
        for who in ('cy','op'):self.error(404,'not_found','GET','/payments/p_z/revisions',who=who)
        self.assertEqual(self.revisions(who='ada'),self.revisions(who='bob'))
        self.error(404,'not_found','GET','/payments/missing/revisions')
        self.error(404,'not_found','POST','/payments/missing/corrections',b,key='unknown')

    def test_original_correction_replay_after_newer_revision_and_precedence(self):
        b={'expected_revision':1,'amount':100,'effective_at':T1,'reason':'x'}
        first=self.good('POST','/payments/p_z/corrections',b,key='same')
        second=self.correction(amount=120,expected=2)
        self.assertGreater(instant(second['recorded_at']),instant(first['recorded_at']))
        self.assertEqual(self.good('POST','/payments/p_z/corrections',b,key='same',status=200),first)
        self.error(409,'idempotency_key_reuse','POST','/payments/p_z/corrections',{'amount':False},key='same')
        self.error(409,'stale_revision','POST','/payments/p_z/corrections',b,key='stale')
        self.correction(amount=121,expected=3,key='stale')

    def test_current_available_insufficiency_before_history_and_rollback(self):
        self.authorize(1100,who='bob',target='cy')
        before=self.snapshot();b={'expected_revision':1,'amount':0,'effective_at':T1,'reason':'refund difference'}
        self.error(409,'insufficient_funds','POST','/payments/p_z/corrections',b,key='retry')
        self.assertEqual(self.snapshot(),before)
        hold=self.good('GET','/authorizations',who='bob',status=200)['authorizations'][0]
        self.good('POST','/authorizations/'+hold['authorization_id']+'/void',{},who='bob',status=200)
        # Release changes current available, not the earlier held boundary.
        self.error(409,'historical_overdraft','POST','/payments/p_z/corrections',b,key='retry')
        self.good('POST','/payments/p_z/corrections',{**b,'amount':150},key='retry')

    def funding_fixture(self):
        d=base.fixture()
        for u,b in zip(d['users'],(0,0,100,0)):u['balance']=b
        d['payments']=[{'id':'p_zfund','from_user_id':'u_bob','to_user_id':'u_ada','amount':100,'note':'fund','visibility':'public','created_at':T2},{'id':'p_aout','from_user_id':'u_ada','to_user_id':'u_cy','amount':100,'note':'out','visibility':'public','created_at':T3}]
        return d

    def test_historical_overdraft_and_current_funds_precedence(self):
        self.reset(self.funding_fixture());before=self.snapshot()
        b={'expected_revision':1,'amount':100,'effective_at':T1,'reason':'move early'}
        self.error(409,'historical_overdraft','POST','/payments/p_aout/corrections',b,key='early')
        self.assertEqual(self.snapshot(),before)
        self.error(409,'insufficient_funds','POST','/payments/p_aout/corrections',{**b,'amount':150},key='early')
        self.assertEqual(self.snapshot(),before)
        self.correction(pid='p_aout',amount=100,effective=T2,key='early')

    def test_combined_same_instant_boundary_not_intermediate_id_order(self):
        self.reset(self.funding_fixture());self.correction(pid='p_aout',amount=100,effective=T2)
        self.assertEqual(self.me(as_of=T2)['balance'],0)
        stmt=self.statement(**{'from':T2,'to':T3})
        self.assertEqual([e['payment']['payment_id'] for e in stmt['entries']],['p_aout','p_zfund'])
        self.assertEqual(stmt['closing_balance'],0)

    def test_historical_hold_overdraft_after_hold_was_voided(self):
        self.reset(base.fixture());p=self.payment(100,who='bob',target='ada')
        hold=self.authorize(150,target='cy');time.sleep(.002)
        self.good('POST','/authorizations/'+hold['authorization_id']+'/void',{},status=200)
        before=self.snapshot();b={'expected_revision':1,'amount':0,'effective_at':p['created_at'],'reason':'historical hold'}
        path='/payments/'+p['payment_id']+'/corrections'
        self.error(409,'historical_overdraft','POST',path,b,who='bob',key='hold-history')
        self.assertEqual(self.snapshot(),before)
        corrected=self.correction(pid=p['payment_id'],amount=70,effective=p['created_at'],who='bob',key='hold-history')
        self.assert_money(self.me(as_of=hold['created_at'],known_at=corrected['recorded_at']),170,150)
        self.assert_money(self.me(as_of=hold['created_at'],known_at=shift(corrected['recorded_at'],-.000001)),200,150)

    def test_historical_partial_final_capture_and_known_events(self):
        self.reset(base.fixture());a=self.authorize();aid=a['authorization_id'];frozen=self.statement()
        time.sleep(.002);p1=self.capture(aid,20,False);time.sleep(.002);p2=self.capture(aid,10,True)
        self.assert_money(self.me(as_of=shift(a['created_at'],-.000001)),100,0)
        self.assert_money(self.me(as_of=a['created_at']),100,80)
        self.assert_money(self.me(as_of=p1['created_at']),80,60)
        self.assert_money(self.me(as_of=p2['created_at']),70,0)
        self.assert_money(self.me(as_of=p2['created_at'],known_at=a['created_at']),100,80)
        self.assert_money(self.me(as_of=p2['created_at'],known_at=p1['created_at']),80,60)
        current=self.hold(aid);self.assertEqual(current['closed_at'],p2['created_at'])
        self.assertEqual(self.statement(snapshot=frozen['snapshot'])['entries'],[])
        self.assertEqual(len(self.statement()['entries']),2)

    def test_void_historical_hold_and_closed_at(self):
        self.reset(base.fixture());a=self.authorize();time.sleep(.002)
        closed=self.good('POST','/authorizations/'+a['authorization_id']+'/void',{},status=200)
        self.assertIsNone(a['closed_at']);self.assertIsNotNone(closed['closed_at'])
        self.assert_money(self.me(as_of=shift(closed['closed_at'],-.000001)),100,80)
        self.assert_money(self.me(as_of=closed['closed_at']),100,0)
        self.assert_money(self.me(as_of=closed['closed_at'],known_at=a['created_at']),100,80)

    def test_future_expiry_deadline_known_after_creation_without_clock_mutation(self):
        self.reset(base.fixture());a=self.authorize();before=self.snapshot()
        self.assert_money(self.me(as_of=shift(a['expires_at'],-.000001),known_at=a['created_at']),100,80)
        self.assert_money(self.me(as_of=a['expires_at'],known_at=a['created_at']),100,0)
        self.assert_money(self.me(as_of=a['expires_at'],known_at=shift(a['created_at'],-.000001)),100,0)
        self.assertEqual(self.snapshot(),before);self.assertEqual(self.hold(a['authorization_id'])['status'],'open')

    def test_seeded_hold_explicit_creation_history(self):
        d=history_fixture();d['authorizations']=[{'id':'a_old','from_user_id':'u_ada','to_user_id':'u_bob','amount':200,'status':'open','created_at':T0,'expires_at':nowshift(3600),'note':'seed','visibility':'public'}]
        self.reset(d)
        self.assert_money(self.me(as_of=T0),1000,200)
        self.assert_money(self.me(as_of=shift(T0,-.000001)),1000,0)
        self.assert_money(self.me(as_of=T2),820,200)

    def test_real_clock_expiry_of_partial_hold_has_exact_closed_time(self):
        d=base.fixture();d['authorization_ttl_seconds']=2;self.reset(d)
        a=self.authorize();p=self.capture(a['authorization_id'],20,False)
        delay=(instant(a['expires_at'])-datetime.datetime.now(UTC)).total_seconds()
        if delay>0:time.sleep(delay+.03)
        h=self.hold(a['authorization_id'])
        self.assertEqual((h['status'],h['closed_at'],h['captured_amount'],h['remaining_amount']),('expired',a['expires_at'],20,0))
        self.assert_money(self.me(as_of=shift(a['expires_at'],-.000001)),80,60)
        self.assert_money(self.me(as_of=a['expires_at']),80,0)
        self.assertEqual([e['payment']['payment_id'] for e in self.statement()['entries']],[p['payment_id']])

    def test_large_money_precision_and_original_direct_payment_replay(self):
        d=base.fixture('BHD',3);d['users'][0]['balance']=2**53-1000000000;self.reset(d)
        body={'to_handle':'bob','amount':1000000000,'note':'original','visibility':'private'}
        original=self.good('POST','/payments',body,key='original')
        self.correction(pid=original['payment_id'],amount=1,effective=original['created_at'])
        self.assertEqual(self.me()['balance'],2**53-1000000000-1)
        self.assertEqual(self.me(as_of=shift(original['created_at'],-.000001))['balance'],2**53-1000000000)
        self.assertEqual(self.good('POST','/payments',body,key='original',status=200),original)
        self.assertEqual(self.statement()['entries'][0]['delta'],-1)

    def test_snapshot_unchanged_during_fifty_competing_writes_and_reads(self):
        initial=self.statement();token=initial['snapshot']
        def operation(i):
            if i%2:return ('read',self.statement(snapshot=token))
            return ('write',self.payment(1,key='write-'+str(i)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:results=list(pool.map(operation,range(50)))
        for kind,value in results:
            if kind=='read':self.assertEqual(value,initial)
        self.assertEqual(self.me()['balance'],795)
        self.assertEqual(self.statement(snapshot=token),initial)

    def test_correction_numeric_equality_unknown_fields_and_key_bounds(self):
        body={'expected_revision':1,'amount':100,'effective_at':T1,'reason':'numeric','extra':{'x':1}}
        path='/payments/p_z/corrections'
        self.error(400,'missing_idempotency_key','POST',path,body)
        self.error(400,'missing_idempotency_key','POST',path,body,key='')
        self.error(422,'validation_failed','POST',path,body,key='k'*256)
        first=self.good('POST',path,body,key='k'*255)
        equal={**body,'expected_revision':1.0,'amount':1e2,'extra':{'x':1.0}}
        self.assertEqual(self.good('POST',path,equal,key='k'*255,status=200),first)
        self.error(409,'idempotency_key_reuse','POST',path,{**body,'extra':{'x':True}},key='k'*255)

    def test_snapshot_window_frozen_across_correction_and_payment(self):
        original=self.statement(**{'from':T1,'to':T2,'limit':1});token=original['snapshot']
        self.correction(effective=T3);self.payment(10)
        again=self.statement(snapshot=token,limit=1,offset=0,ignored='ok')
        for f in ('opening_balance','closing_balance','entries','has_more'):self.assertEqual(again[f],original[f])
        self.assertEqual(self.statement(**{'from':T1,'to':T2})['entries'],[])
        self.assertFalse(self.statement(snapshot=token,offset=1)['has_more'])
        self.assertEqual(self.statement(snapshot=token,offset=999)['entries'],[])

    def test_snapshot_permissions_filter_exclusivity_reset_and_export_restore(self):
        snap=self.statement()['snapshot'];before=self.snapshot()
        for field in ('from','to','known_at'):self.error(422,'validation_failed','GET',query('/statement',snapshot=snap,**{field:T1}))
        self.error(404,'not_found','GET',query('/statement',snapshot=snap),who='bob')
        self.error(404,'not_found','GET','/statement?snapshot=missing')
        self.assertEqual(base.http('POST','/_test/reset',history_fixture())[0],204)
        oldtokens=dict(self.tokens);self.reset(history_fixture())
        self.error(404,'not_found','GET',query('/statement',snapshot=snap))
        self.assertEqual(base.http('POST','/_test/import',before)[0],204);self.tokens=oldtokens
        self.assertEqual(len(self.statement(snapshot=snap)['entries']),3)

    def test_concurrent_corrections_expected_revision_compare_and_swap(self):
        b={'expected_revision':1,'amount':201,'effective_at':T1,'reason':'CAS'}
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:r=list(pool.map(lambda i:self.call('POST','/payments/p_z/corrections',b,key='cas'+str(i)),range(50)))
        self.assertEqual(sum(s==201 for s,_ in r),1)
        self.assertEqual(sum(s==409 and v['error']['code']=='stale_revision' for s,v in r),49)
        self.assertEqual(len(self.revisions()),2);self.assertEqual(self.me()['balance'],819)

    def test_concurrent_identical_correction_key_exactly_once(self):
        b={'expected_revision':1,'amount':201,'effective_at':T1,'reason':'same'}
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:r=list(pool.map(lambda _:self.call('POST','/payments/p_z/corrections',b,key='same'),range(50)))
        self.assertEqual(sum(s==201 for s,_ in r),1);self.assertEqual(sum(s==200 for s,_ in r),49)
        self.assertTrue(all(v==r[0][1] for _,v in r));self.assertEqual(self.me()['balance'],819)

    def test_linked_capture_and_settlement_immutable_original_histories(self):
        settlement=self.good('POST','/settlements',{'transfers':[{'from_handle':'ada','to_handle':'bob','amount':10},{'from_handle':'bob','to_handle':'cy','amount':5}]},who='op',key='net')
        for payment in settlement['payments']:
            self.assertEqual(self.revisions(payment['payment_id'],who='bob')[0]['recorded_at'],settlement['committed_at'])
            sender=payment['from_handle'];b={'expected_revision':1,'amount':0,'effective_at':T1,'reason':'linked'}
            self.error(422,'linked_payment_immutable','POST','/payments/'+payment['payment_id']+'/corrections',b,who=sender,key='linked')
        a=self.authorize();p=self.capture(a['authorization_id'],10,True)
        self.error(422,'linked_payment_immutable','POST','/payments/'+p['payment_id']+'/corrections',b,key='capture-linked')

    def test_request_payment_is_correctable_without_reopening_request(self):
        request=self.request(20);rid=request['request_id'];body={'visibility':'private'}
        p=self.good('POST','/requests/'+rid+'/pay',body,key='paid')
        self.correction(pid=p['payment_id'],amount=10,effective=p['created_at'])
        self.assertEqual(self.good('POST','/requests/'+rid+'/pay',body,key='paid',status=200),p)
        current=next(x for x in self.good('GET','/requests',status=200)['requests'] if x['request_id']==rid)
        self.assertEqual((current['status'],current['payment_id']),('paid',p['payment_id']))

    def test_bitemporal_oracle_random_revisions_conservation_and_statements(self):
        fixture=history_fixture();parties={p['id']:(p['from_user_id'],p['to_user_id']) for p in fixture['payments']}
        opening={'u_ada':1000,'u_bob':1000,'u_cy':1000,'u_op':0}
        revisions={p['id']:[{'revision':1,'amount':p['amount'],'effective_at':p['created_at'],'recorded_at':p['created_at']}] for p in fixture['payments']}
        randomizer=random.Random(37019);known=[T0,T1,T2]
        for i in range(12):
            pid=randomizer.choice(list(parties));sender=parties[pid][0][2:]
            r=self.correction(pid=pid,amount=randomizer.randrange(301),effective=randomizer.choice([T1,T2,T3]),expected=len(revisions[pid]),who=sender)
            revisions[pid].append(r);known.append(r['recorded_at'])
        for k in known:
            for at in (T0,T1,T2,T3):
                values=[]
                for who in ('ada','bob','cy','op'):
                    actual=self.me(who,as_of=at,known_at=k)['balance'];expected=oracle_balance('u_'+who,opening,parties,revisions,at,k)
                    self.assertEqual(actual,expected);values.append(actual)
                self.assertEqual(sum(values),3000)
            stmt=self.statement(known_at=k,**{'from':T0,'to':T4,'limit':200})
            chosen=[(pid,r) for pid,r in selected(revisions,k) if 'u_ada' in parties[pid]]
            chosen.sort(key=lambda x:(instant(x[1]['effective_at']),x[0]))
            self.assertEqual([(e['payment']['payment_id'],e['revision'],e['payment']['amount']) for e in stmt['entries']],[(pid,r['revision'],r['amount']) for pid,r in chosen])
            running=1000
            for entry,(pid,r) in zip(stmt['entries'],chosen):
                sender,receiver=parties[pid];delta=r['amount']*(('u_ada'==receiver)-('u_ada'==sender));running+=delta
                self.assertEqual((entry['delta'],entry['balance_after']),(delta,running))
            self.assertEqual(stmt['closing_balance'],running)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--revision',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();base.BASE=a.url;out=Path(a.out);out.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    with (out/'tests.log').open('w') as stream:r=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Stage3))
    report={'revision':a.revision,'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'seconds':time.monotonic()-started,'requests':len(base.REQUEST_TIMES),'max_request_seconds':max((x['seconds'] for x in base.REQUEST_TIMES),default=0),'failed_tests':[t.id() for t,_ in r.failures+r.errors]}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));raise SystemExit(0 if r.wasSuccessful() and r.testsRun and not r.skipped else 1)
