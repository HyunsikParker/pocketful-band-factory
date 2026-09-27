"""Independent browser acceptance derived from stage-2 UX and recovery contract.

Destructive test fixture resets target; use a disposable stage-2 container only.
"""
import argparse
import datetime
import json
from pathlib import Path
import time
import unittest
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect
import stage1_acceptance as api

OUT=None
EXECUTABLE=None
INSECURE=False


def ui_fixture(currency='EUR',minor=2):
    data=api.fixture(currency,minor)
    data['users'][0]['balance']=10000
    data['users'][1]['balance']=20000
    return data


class BrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        options={'headless':True}
        if EXECUTABLE:
            options['executable_path']=EXECUTABLE
        cls.browser=cls.pw.chromium.launch(**options)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.assertEqual(api.http('POST','/_test/reset',ui_fixture())[0],204)
        self.context=self.browser.new_context(viewport={'width':1280,'height':900})
        self.page=self.context.new_page()
        self.page.set_default_timeout(5000)
        self.external=[]
        self.page.on('request',lambda r:self.external.append(r.url) if urlsplit(r.url).scheme in ('http','https') and urlsplit(r.url).netloc!=urlsplit(api.BASE).netloc else None)
        self.login()
        if INSECURE:
            self.assertFalse(self.page.evaluate('isSecureContext'))
            self.assertEqual(self.page.evaluate('typeof crypto.randomUUID'),'undefined')

    def tearDown(self):
        self.page.screenshot(path=str(OUT/(self._testMethodName+'.png')),full_page=True)
        self.context.close()

    def el(self,name):
        return self.page.get_by_test_id(name)

    def login(self,who='ada'):
        if self.el('logout-button').count() and self.el('logout-button').is_visible():
            self.el('logout-button').click()
        self.page.goto(api.BASE+'/login')
        self.el('login-email').fill(who+'@example.test')
        self.el('login-password').fill(api.PASSWORD)
        self.el('login-submit').click()
        expect(self.el('current-user')).to_contain_text(who.title())
        self.page.goto(api.BASE+'/')
        expect(self.el('wallet-balance')).to_be_visible()

    def token(self,who):
        status,response=api.http('POST','/auth/login',{'email':who+'@example.test','password':api.PASSWORD})
        self.assertEqual(status,200)
        return response['token']

    def form(self,amount='1.00',note='coffee',handle='bob'):
        self.el('pay-handle').fill(handle)
        self.el('pay-amount').fill(amount)
        self.el('pay-note').fill(note)
        self.el('pay-visibility').select_option('private')

    def balance(self,minor):
        expect(self.el('wallet-balance')).to_have_attribute('data-amount',str(minor))

    def test_navigation_identity_logout_and_content_negotiation(self):
        for route in ('/','/requests','/split','/authorizations','/signup','/login'):
            self.page.goto(api.BASE+route)
            expect(self.el('current-user')).to_contain_text('Ada')
            expect(self.el('current-handle')).to_have_text('ada')
        self.el('logout-button').click()
        expect(self.el('current-user')).to_have_count(0)
        token=self.token('ada')
        for path,field in [('/requests','requests'),('/authorizations','authorizations')]:
            status,response=api.http('GET',path,token=token)
            self.assertEqual(status,200)
            self.assertIn(field,response)

    def test_signup_login_errors_and_new_account_empty_states(self):
        self.el('logout-button').click()
        self.page.goto(api.BASE+'/signup')
        self.el('signup-email').fill('ada@example.test')
        self.el('signup-password').fill(api.PASSWORD)
        self.el('signup-display-name').fill('New Viewer')
        self.el('signup-submit').click()
        expect(self.el('auth-error')).to_be_visible()
        self.el('signup-email').fill('viewer@example.test')
        self.el('signup-submit').click()
        expect(self.el('current-user')).to_contain_text('New Viewer')
        expect(self.el('auth-error')).to_have_count(0)
        self.page.goto(api.BASE+'/')
        expect(self.el('wallet-available')).to_have_text('0.00 EUR')
        expect(self.el('empty-activity')).to_be_visible()
        self.page.goto(api.BASE+'/requests')
        expect(self.el('empty-requests')).to_be_visible()
        self.page.goto(api.BASE+'/authorizations')
        expect(self.el('empty-authorizations')).to_be_visible()
        self.el('logout-button').click()
        self.page.goto(api.BASE+'/login')
        self.el('login-email').fill('viewer@example.test')
        self.el('login-password').fill('wrong password')
        self.el('login-submit').click()
        expect(self.el('auth-error')).to_be_visible()
        self.el('login-password').fill(api.PASSWORD)
        self.el('login-submit').click()
        expect(self.el('current-user')).to_contain_text('New Viewer')
        expect(self.el('auth-error')).to_have_count(0)

    def test_same_form_replay_and_same_numeric_value_edit_is_new_attempt(self):
        attempts=[]
        self.page.on('request',lambda r:attempts.append((r.headers.get('idempotency-key'),r.post_data_json)) if r.method=='POST' and urlsplit(r.url).path=='/payments' else None)
        self.form(amount='1')
        self.el('pay-submit').click()
        self.balance(9900)
        expect(self.el('pay-amount')).to_have_value('1')
        self.el('pay-submit').click()
        self.balance(9900)
        expect(self.el('pay-error')).to_have_count(0)
        before=len(attempts)
        self.el('pay-amount').fill('1.00')
        self.el('pay-submit').click()
        self.balance(9800)
        self.assertGreater(len(attempts),before)
        self.assertNotEqual(attempts[0][0],attempts[-1][0])
        self.assertEqual(attempts[0][1],attempts[-1][1])
        self.assertFalse(self.external)

    def test_decimal_validation_no_post_and_currency_formatting(self):
        for currency,minor,text,amount in [('JPY',0,'15',15),('EUR',2,'15.5',1550),('BHD',3,'1.025',1025)]:
            self.assertEqual(api.http('POST','/_test/reset',ui_fixture(currency,minor))[0],204)
            self.login()
            attempted=[]
            def record(req):
                if req.method=='POST' and urlsplit(req.url).path=='/payments':
                    attempted.append(req.post_data_json)
            self.page.on('request',record)
            self.form(amount='')
            self.el('pay-amount').press_sequentially('not a number')
            self.el('pay-submit').click()
            expect(self.el('pay-error')).to_be_visible()
            self.assertEqual(attempted,[])
            self.el('pay-amount').fill('1.'+'0'*(minor+1))
            self.el('pay-submit').click()
            expect(self.el('pay-error')).to_be_visible()
            self.assertEqual(attempted,[])
            self.el('pay-amount').fill(text)
            self.el('pay-submit').click()
            self.balance(10000-amount)
            self.assertEqual(attempted[-1]['amount'],amount)
            value=10000-amount
            expected=str(value) if minor==0 else str(value//(10**minor))+'.'+str(value%(10**minor)).zfill(minor)
            expect(self.el('wallet-balance')).to_have_text(expected+' '+currency)
            self.page.remove_listener('request',record)

    def test_refused_stale_funds_refreshes_without_clearing_form(self):
        self.form(amount='2.00',note='preserved')
        self.assertEqual(api.http('POST','/payments',{'to_handle':'bob','amount':9999},token=self.token('ada'),key='other-client')[0],201)
        self.el('pay-submit').click()
        expect(self.el('pay-error')).to_be_visible()
        self.balance(1)
        expect(self.el('pay-handle')).to_have_value('bob')
        expect(self.el('pay-amount')).to_have_value('2.00')
        expect(self.el('pay-note')).to_have_value('preserved')
        expect(self.el('pay-visibility')).to_have_value('private')

    def test_lost_committed_response_retries_identical_key_and_body(self):
        attempts=[]
        def intercept(route):
            req=route.request
            if req.method!='POST':
                return route.continue_()
            attempts.append((req.headers.get('idempotency-key'),req.post_data_json))
            if len(attempts)==1:
                response=route.fetch()
                self.assertEqual(response.status,201)
                route.abort('failed')
            else:
                route.continue_()
        self.page.route('**/payments',intercept)
        self.form(amount='1.25',note='uncertain')
        self.el('pay-submit').click()
        expect(self.el('pay-uncertain')).to_be_visible()
        expect(self.el('pay-uncertain')).not_to_have_text('')
        expect(self.el('pay-error')).to_have_count(0)
        expect(self.el('pay-note')).to_have_value('uncertain')
        self.el('pay-submit').click()
        self.balance(9875)
        expect(self.el('pay-error')).to_have_count(0)
        expect(self.el('pay-uncertain')).to_have_count(0)
        self.assertEqual(len(attempts),2)
        self.assertEqual(attempts[0],attempts[1])
        feed=api.http('GET','/activity',token=self.token('ada'))[1]['payments']
        self.assertEqual(len(feed),1)

    def test_latest_refresh_wins_old_balance_and_feed_response(self):
        delayed={}
        counts={}
        def intercept(route):
            path=urlsplit(route.request.url).path
            counts[path]=counts.get(path,0)+1
            response=route.fetch()
            if counts[path]==1:
                delayed[path]=(route,response)
            else:
                route.fulfill(response=response)
        self.page.route('**/me',intercept)
        self.page.route('**/activity*',intercept)
        self.form(note='keep during refresh')
        self.el('wallet-refresh').click(no_wait_after=True)
        for _ in range(40):
            if '/me' in delayed:
                break
            self.page.wait_for_timeout(25)
        self.assertIn('/me',delayed)
        self.assertEqual(api.http('POST','/payments',{'to_handle':'bob','amount':300},token=self.token('ada'),key='refresh-change')[0],201)
        self.el('wallet-refresh').click(no_wait_after=True)
        self.balance(9700)
        for route,response in list(delayed.values()):
            route.fulfill(response=response)
        self.page.wait_for_timeout(150)
        self.balance(9700)
        expect(self.el('pay-note')).to_have_value('keep during refresh')
        expect(self.el('empty-activity')).to_have_count(0)

    def test_stale_cancelled_request_removes_pay_button(self):
        bob=self.token('bob')
        status,request=api.http('POST','/requests',{'payer_handle':'ada','amount':100},token=bob,key='request')
        self.assertEqual(status,201)
        rid=request['request_id']
        self.page.goto(api.BASE+'/requests')
        expect(self.el('request-pay-'+rid)).to_be_visible()
        self.assertEqual(api.http('POST','/requests/'+rid+'/cancel',{},token=bob)[0],200)
        self.el('request-pay-'+rid).click()
        expect(self.el('request-error')).to_be_visible()
        expect(self.el('request-pay-'+rid)).to_have_count(0)
        expect(self.el('request-item-'+rid)).to_have_attribute('data-status','cancelled')

    def test_request_form_outgoing_cancel_incoming_decline_and_pay(self):
        self.el('request-handle').fill('bob')
        self.el('request-amount').fill('2.50')
        self.el('request-note').fill('shared taxi')
        with self.page.expect_response(lambda r:urlsplit(r.url).path=='/requests' and r.request.method=='POST') as response:
            self.el('request-submit').click()
        request=response.value.json()
        self.assertEqual((response.value.status,request['amount']), (201,250))
        rid=request['request_id']
        self.page.goto(api.BASE+'/requests')
        expect(self.el('request-amount-'+rid)).to_have_text('2.50 EUR')
        expect(self.el('request-pay-'+rid)).to_have_count(0)
        self.el('request-cancel-'+rid).click()
        expect(self.el('request-item-'+rid)).to_have_attribute('data-status','cancelled')
        bob=self.token('bob')
        ids=[]
        for n in range(2):
            status,value=api.http('POST','/requests',{'payer_handle':'ada','amount':100},token=bob,key='ui-action'+str(n))
            self.assertEqual(status,201)
            ids.append(value['request_id'])
        self.page.goto(api.BASE+'/requests')
        self.el('request-decline-'+ids[0]).click()
        expect(self.el('request-item-'+ids[0])).to_have_attribute('data-status','declined')
        self.el('request-pay-'+ids[1]).click()
        expect(self.el('request-item-'+ids[1])).to_have_attribute('data-status','paid')
        self.page.goto(api.BASE+'/')
        self.balance(9900)

    def test_split_preview_order_zero_shares_and_submitted_identity(self):
        self.page.goto(api.BASE+'/split')
        self.el('split-amount').fill('0.01')
        self.el('split-handles').fill('cy,ada,bob')
        self.el('split-note').fill('  나누기 😀  ')
        expect(self.el('split-preview')).to_be_visible()
        expect(self.el('split-share-cy')).to_have_text('0.01 EUR')
        expect(self.el('split-share-ada')).to_have_text('0.00 EUR')
        expect(self.el('split-share-bob')).to_have_text('0.00 EUR')
        with self.page.expect_response(lambda r:urlsplit(r.url).path=='/splits' and r.request.method=='POST') as response:
            self.el('split-submit').click()
        self.assertEqual(response.value.status,201)
        self.assertEqual(response.value.json()['shares'],[{'handle':'cy','amount':1},{'handle':'ada','amount':0},{'handle':'bob','amount':0}])

    def test_authorization_ui_seeded_available_and_capture(self):
        self.el('authorize-handle').fill('bob')
        self.el('authorize-amount').fill('20.00')
        self.el('authorize-note').fill('deposit')
        self.el('authorize-visibility').select_option('private')
        with self.page.expect_response(lambda r:urlsplit(r.url).path=='/authorizations' and r.request.method=='POST') as response:
            self.el('authorize-submit').click()
        auth=response.value.json()
        aid=auth['authorization_id']
        expect(self.el('wallet-available')).to_have_text('80.00 EUR')
        expect(self.el('wallet-held')).to_have_text('20.00 EUR')
        expect(self.el('wallet-balance')).to_have_text('100.00 EUR')
        self.login('bob')
        self.page.goto(api.BASE+'/authorizations')
        expect(self.el('authorization-capture-amount-'+aid)).to_have_value('20.00')
        expect(self.el('authorization-void-'+aid)).to_have_count(0)
        expect(self.el('authorization-expires-'+aid)).to_have_text(auth['expires_at'])
        self.el('authorization-capture-amount-'+aid).fill('5.00')
        self.el('authorization-capture-'+aid).click()
        expect(self.el('authorization-item-'+aid)).to_have_attribute('data-status','captured')
        expect(self.el('authorization-captured-'+aid)).to_have_text('5.00 EUR')
        self.login('ada')
        expect(self.el('wallet-available')).to_have_text('95.00 EUR')
        expect(self.el('wallet-held')).to_have_count(0)

    def test_seeded_hold_and_maximum_balance_exact_display(self):
        fixture=ui_fixture('BHD',3)
        value=2**53
        fixture['users'][0]['balance']=value
        expiry=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=2)).isoformat()
        fixture['authorizations']=[{'id':'seed_hold','from_user_id':'u_ada','to_user_id':'u_bob','amount':2000,'note':'seed','visibility':'private','status':'open','expires_at':expiry}]
        self.assertEqual(api.http('POST','/_test/reset',fixture)[0],204)
        self.login()
        def money(n):
            return str(n//1000)+'.'+str(n%1000).zfill(3)+' BHD'
        expect(self.el('wallet-balance')).to_have_text(money(value))
        expect(self.el('wallet-balance')).to_have_attribute('data-amount',str(value))
        expect(self.el('wallet-available')).to_have_text(money(value-2000))
        expect(self.el('wallet-held')).to_have_text('2.000 BHD')
        self.page.goto(api.BASE+'/authorizations')
        expect(self.el('authorization-item-seed_hold')).to_have_attribute('data-status','open')
        expect(self.el('authorization-void-seed_hold')).to_be_visible()
        expect(self.el('authorization-capture-seed_hold')).to_have_count(0)

    def test_responsive_routes_labels_and_safe_verbatim_notes(self):
        token=self.token('ada')
        note='<img src=x onerror="window.injected=true"> 한글 😀'
        status,payment=api.http('POST','/payments',{'to_handle':'bob','amount':100,'note':note,'visibility':'private'},token=token,key='visual')
        self.assertEqual(status,201)
        observations=[]
        for width in (375,1280):
            self.page.set_viewport_size({'width':width,'height':900})
            for route in ('/','/requests','/split','/authorizations','/signup','/login'):
                self.page.goto(api.BASE+route)
                expect(self.el('current-user')).to_be_visible()
                self.page.wait_for_timeout(100)
                metrics=self.page.evaluate('''() => ({width:innerWidth,scroll:document.documentElement.scrollWidth,unlabelled:[...document.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(e=>e.getBoundingClientRect().width>0 && !(e.labels && [...e.labels].some(l=>l.innerText.trim()))).map(e=>e.getAttribute('data-testid')||e.tagName)})''')
                self.assertLessEqual(metrics['scroll'],metrics['width'],route)
                self.assertEqual(metrics['unlabelled'],[],route)
                observations.append({'width':width,'route':route,**metrics})
                label='home' if route=='/' else route[1:]
                self.page.screenshot(path=str(OUT/('visual-'+str(width)+'-'+label+'.png')),full_page=True)
            self.page.goto(api.BASE+'/')
            expect(self.el('activity-note-'+payment['payment_id'])).to_have_text(note)
            self.assertEqual(self.el('activity-note-'+payment['payment_id']).text_content(),note)
            self.assertEqual(self.el('activity-note-'+payment['payment_id']).locator('img').count(),0)
            self.assertFalse(self.page.evaluate('Boolean(window.injected)'))
        self.assertFalse(self.external)
        (OUT/'layout-metrics.json').write_text(json.dumps(observations,indent=2))


def main():
    global OUT,EXECUTABLE,INSECURE
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--out',required=True)
    parser.add_argument('--browser-executable')
    parser.add_argument('--insecure-host',action='store_true',help='Disposable root-owned Docker runner only: map pocketful-service to its shared loopback')
    args=parser.parse_args()
    api.BASE=args.url.rstrip('/')
    if args.insecure_host:
        with open('/etc/hosts','a') as hosts:hosts.write('\n127.0.0.1 pocketful-service\n')
        api.BASE=api.BASE.replace('127.0.0.1','pocketful-service')
        INSECURE=True
    OUT=Path(args.out)
    OUT.mkdir(parents=True,exist_ok=False)
    EXECUTABLE=args.browser_executable
    started=time.monotonic()
    with (OUT/'tests.log').open('w') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BrowserAcceptance))
    report={'revision':args.revision,'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),'seconds':time.monotonic()-started,'failed_tests':[t.id() for t,_ in result.failures+result.errors]}
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    raise SystemExit(0 if result.wasSuccessful() and result.testsRun and not result.skipped else 1)


if __name__=='__main__':
    main()
