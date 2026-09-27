"""Independent Stage3 browser tests against agreed UI contract, not implementation.

Run only on disposable synthetic HTTP service; screenshot/metrics contain no tokens.
"""
import argparse,json,time,unittest,re
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import expect
import stage1_acceptance as api
import stage2_browser as previous
import stage3_api as temporal

OUT=None

class Stage3Browser(unittest.TestCase):
    setUpClass=classmethod(previous.BrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(previous.BrowserAcceptance.tearDownClass.__func__)
    el=previous.BrowserAcceptance.el
    login=previous.BrowserAcceptance.login
    token=previous.BrowserAcceptance.token

    def setUp(self):
        assert api.http('POST','/_test/reset',temporal.history_fixture())[0]==204
        self.context=self.browser.new_context(viewport={'width':1280,'height':900})
        self.page=self.context.new_page();self.page.set_default_timeout(5000)
        self.errors=[];self.external=[]
        self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        self.page.on('request',lambda r:self.external.append(r.url) if urlsplit(r.url).scheme in ('http','https') and urlsplit(r.url).netloc!=urlsplit(api.BASE).netloc else None)
        self.login()
        if previous.INSECURE:self.assertFalse(self.page.evaluate('isSecureContext'))

    def tearDown(self):
        self.page.screenshot(path=str(OUT/(self._testMethodName+'.png')),full_page=True)
        self.context.close();self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])

    def history(self):
        self.page.goto(api.BASE+'/history?payment=p_z')
        expect(self.el('original-receipt')).to_be_visible()
        expect(self.el('correction-amount')).to_be_visible()

    def correction_form(self,amount='1.00'):
        self.el('correction-amount').fill(amount)
        self.el('correction-effective').fill(temporal.T1)
        self.el('correction-reason').fill('  independent 정정 😀  ')

    def submit_revision(self,number):
        self.el('correction-submit').click()
        expect(self.el('revision-'+str(number))).to_be_visible()

    def test_new_routes_keep_identity_and_original_receipt_after_zero(self):
        for route in ('/statements','/history'):
            self.page.goto(api.BASE+route);expect(self.el('current-user')).to_contain_text('Ada')
            expect(self.el('current-handle')).to_have_text('ada')
        self.history();original=self.el('original-receipt').inner_text()
        self.assertIn('2.00 EUR',original)
        self.correction_form('0');self.submit_revision(2)
        expect(self.el('original-receipt')).to_have_text(original)
        expect(self.el('revision-2')).to_contain_text('0.00 EUR')
        self.page.goto(api.BASE+'/');expect(self.el('activity-amount-p_z')).to_have_text('2.00 EUR')

    def test_decimal_and_offset_validation_no_correction_post(self):
        self.history();posts=[]
        self.page.on('request',lambda r:posts.append(r.url) if r.method=='POST' and r.url.endswith('/corrections') else None)
        for value in ('1.005','abc','-1'):
            self.correction_form(value);self.el('correction-submit').click()
            expect(self.el('correction-error')).to_be_visible()
        self.correction_form();self.el('correction-effective').fill('2020-01-01T01:00:00')
        self.el('correction-submit').click();expect(self.el('correction-error')).to_be_visible()
        self.assertEqual(posts,[])

    def test_uncertain_correction_retry_same_body_key_and_original_receipt(self):
        self.history();self.correction_form();attempts=[];drop=[True]
        def intercepted(route):
            request=route.request
            if request.method!='POST':return route.continue_()
            attempts.append((request.headers.get('idempotency-key'),request.post_data_json))
            response=route.fetch()
            if drop[0]:drop[0]=False;route.abort('failed')
            else:route.fulfill(response=response)
        self.page.route('**/payments/p_z/corrections',intercepted)
        self.el('correction-submit').click();expect(self.el('correction-uncertain')).to_be_visible()
        expect(self.el('correction-error')).to_have_count(0)
        expect(self.el('correction-amount')).to_have_value('1.00')
        self.submit_revision(2)
        expect(self.el('correction-uncertain')).to_have_count(0)
        self.assertEqual(len(attempts),2);self.assertEqual(attempts[0],attempts[1])
        s,body=api.http('GET','/payments/p_z/revisions',token=self.token('ada'))
        self.assertEqual(s,200);self.assertEqual(len(body['revisions']),2)

    def test_stale_refusal_preserves_form_until_explicit_latest(self):
        self.history();self.correction_form('1.20')
        token=self.token('ada');body={'expected_revision':1,'amount':150,'effective_at':temporal.T1,'reason':'other client'}
        self.assertEqual(api.http('POST','/payments/p_z/corrections',body,token,'other')[0],201)
        self.el('correction-submit').click();expect(self.el('correction-error')).to_be_visible()
        expect(self.el('correction-amount')).to_have_value('1.20')
        expect(self.el('correction-expected')).to_have_value('1')
        self.el('correction-use-latest').click();expect(self.el('correction-expected')).to_have_value('2')
        self.submit_revision(3)

    def test_statement_paging_freezes_after_correction_and_refreshes_explicitly(self):
        self.page.goto(api.BASE+'/statements')
        self.el('statement-limit').select_option('1');self.el('statement-submit').click()
        expect(self.el('statement-item-p_z')).to_be_visible()
        old=self.el('statement-snapshot').inner_text();closing=self.el('statement-closing').inner_text()
        token=self.token('ada');body={'expected_revision':1,'amount':0,'effective_at':temporal.T3,'reason':'move'}
        self.assertEqual(api.http('POST','/payments/p_z/corrections',body,token,'move')[0],201)
        self.el('statement-next').click();expect(self.el('statement-item-p_a')).to_be_visible()
        self.el('statement-previous').click();expect(self.el('statement-item-p_z')).to_be_visible()
        expect(self.el('statement-snapshot')).to_have_text(old);expect(self.el('statement-closing')).to_have_text(closing)
        self.el('statement-submit').click();expect(self.el('statement-item-p_a')).to_be_visible()
        expect(self.el('statement-closing')).to_contain_text('10.20 EUR')

    def test_historical_balance_known_filters(self):
        self.page.goto(api.BASE+'/history')
        self.el('history-as-of').fill(temporal.T1);self.el('history-known-at').fill(temporal.T0)
        self.el('history-balance-submit').click();expect(self.el('history-total')).to_contain_text('10.00 EUR')
        self.el('history-known-at').fill(temporal.T1);self.el('history-balance-submit').click()
        expect(self.el('history-total')).to_contain_text('8.00 EUR');expect(self.el('history-available')).to_contain_text('8.00 EUR')

    def test_new_screens_mobile_desktop_labels_focus_and_negative_statement(self):
        # Same-instant combined balance is solvent although ID-ordered entry is negative.
        data=temporal.history_fixture()
        for u,n in zip(data['users'],(0,0,100,0)):u['balance']=n
        data['payments']=[{'id':'p_zfund','from_user_id':'u_bob','to_user_id':'u_ada','amount':100,'created_at':temporal.T1,'visibility':'public','note':''},{'id':'p_aout','from_user_id':'u_ada','to_user_id':'u_cy','amount':100,'created_at':temporal.T1,'visibility':'public','note':''}]
        self.assertEqual(api.http('POST','/_test/reset',data)[0],204);self.login()
        metrics=[]
        for width in (375,1280):
            self.page.set_viewport_size({'width':width,'height':900})
            for route in ('/statements','/history?payment=p_aout'):
                self.page.goto(api.BASE+route);expect(self.el('current-user')).to_be_visible()
                if route=='/statements':
                    expect(self.el('statement-after-p_aout')).to_be_visible()
                    self.assertIn(self.el('statement-after-p_aout').inner_text(),('-1.00 EUR','−1.00 EUR'))
                else:expect(self.el('original-receipt')).to_be_visible()
                details=self.page.evaluate("""() => ({width:innerWidth,scroll:document.documentElement.scrollWidth,unlabelled:[...document.querySelectorAll('input,select,textarea')].filter(e=>e.getBoundingClientRect().width>0&&!e.labels?.length).map(e=>e.getAttribute('data-testid'))})""")
                self.assertLessEqual(details['scroll'],width);self.assertEqual(details['unlabelled'],[])
                self.page.keyboard.press('Tab')
                focused=self.page.evaluate("""() => ({tag:document.activeElement.tagName,outline:getComputedStyle(document.activeElement).outlineStyle,width:getComputedStyle(document.activeElement).outlineWidth})""")
                self.assertNotEqual(focused['tag'],'BODY');self.assertNotEqual(focused['outline'],'none')
                metrics.append({'viewport':width,'route':route,**details,'focus':focused})
                self.page.screenshot(path=str(OUT/('visual-'+str(width)+'-'+route.split('?')[0][1:]+'.png')),full_page=True)
        (OUT/'layout.json').write_text(json.dumps(metrics,indent=2)+'\n')

    def test_actual_net_settlement_signed_entry_and_new_screen_contrast(self):
        data=api.fixture()
        for user in data['users']:user['balance']=0
        self.assertEqual(api.http('POST','/_test/reset',data)[0],204)
        status,batch=api.http('POST','/settlements',{'transfers':[{'from_handle':'ada','to_handle':'bob','amount':1},{'from_handle':'bob','to_handle':'ada','amount':1}]},self.token('op'),'cycle')
        self.assertEqual(status,201);first=min(batch['payments'],key=lambda p:p['payment_id']);self.login(first['from_handle'])
        colors=[]
        def contrast(fg,bg):
            def lum(c):
                values=[int(x)/255 for x in re.findall(r'\d+',c)[:3]]
                values=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in values]
                return sum(v*w for v,w in zip(values,[.2126,.7152,.0722]))
            low,high=sorted((lum(fg),lum(bg)));return (high+.05)/(low+.05)
        for path in ['/statements','/history?payment='+first['payment_id']]:
            self.page.goto(api.BASE+path)
            if path=='/statements':
                value=self.el('statement-after-'+first['payment_id']);expect(value).to_be_visible()
                self.assertIn(value.inner_text(),('-0.01 EUR','−0.01 EUR'))
            else:expect(self.el('original-receipt')).to_be_visible()
            palette=self.page.evaluate("""() => [...document.querySelectorAll('.date-exact,.record-money,.original-receipt,.snapshot-label,.history-money dd,.subtext,.primary,.secondary')].filter(e=>e.getBoundingClientRect().width).map(e=>{let n=e,bg;while(n){bg=getComputedStyle(n).backgroundColor;if(bg!=='rgba(0, 0, 0, 0)'&&bg!=='transparent')break;n=n.parentElement;}return {fg:getComputedStyle(e).color,bg:bg||'rgb(255,255,255)',class:e.className};})""")
            for c in palette:c['ratio']=contrast(c['fg'],c['bg']);self.assertGreaterEqual(c['ratio'],4.5)
            colors.extend(palette)
        (OUT/'new-screen-contrast.json').write_text(json.dumps({'pairs':len(colors),'minimum':min(c['ratio'] for c in colors),'colors':colors},indent=2)+'\n')

def main():
    global OUT
    p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--revision',required=True);p.add_argument('--out',required=True);p.add_argument('--browser-executable');p.add_argument('--insecure-host',action='store_true')
    a=p.parse_args();api.BASE=a.url.rstrip('/');previous.EXECUTABLE=a.browser_executable
    if a.insecure_host:
        with open('/etc/hosts','a') as hosts:hosts.write('\n127.0.0.1 pocketful-service\n')
        api.BASE=api.BASE.replace('127.0.0.1','pocketful-service');previous.INSECURE=True
    OUT=Path(a.out);OUT.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    with (OUT/'tests.log').open('w') as stream:r=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Stage3Browser))
    report={'revision':a.revision,'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'seconds':time.monotonic()-start,'failed_tests':[t.id() for t,_ in r.failures+r.errors]}
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));raise SystemExit(0 if r.wasSuccessful() and r.testsRun and not r.skipped else 1)

if __name__=='__main__':main()
