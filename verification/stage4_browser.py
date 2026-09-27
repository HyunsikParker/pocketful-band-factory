"""Independent Stage4 UI contract checks: normal HTTP, refund/batch recovery."""
import argparse,json,re,time,unittest
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import expect
import stage1_acceptance as api
import stage2_browser as previous
import stage3_api as temporal

OUT=None
class Stage4Browser(unittest.TestCase):
    setUpClass=classmethod(previous.BrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(previous.BrowserAcceptance.tearDownClass.__func__)
    el=previous.BrowserAcceptance.el;login=previous.BrowserAcceptance.login;token=previous.BrowserAcceptance.token
    def setUp(self):
        assert api.http('POST','/_test/reset',temporal.history_fixture())[0]==204
        self.context=self.browser.new_context(viewport={'width':1280,'height':900});self.page=self.context.new_page();self.page.set_default_timeout(5000)
        self.errors=[];self.external=[];self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        self.page.on('request',lambda r:self.external.append(r.url) if urlsplit(r.url).scheme in ('http','https') and urlsplit(r.url).netloc!=urlsplit(api.BASE).netloc else None)
        self.login('bob')
        if previous.INSECURE:self.assertFalse(self.page.evaluate('isSecureContext'))
    def tearDown(self):
        self.page.screenshot(path=str(OUT/(self._testMethodName+'.png')),full_page=True)
        self.context.close();self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
    def receipt(self,pid='p_z'):
        self.page.goto(api.BASE+'/history?payment='+pid);expect(self.el('original-receipt')).to_be_visible();expect(self.el('refund-amount')).to_be_visible()
    def operations(self):
        self.login('op');self.page.goto(api.BASE+'/operations');expect(self.el('batch-role')).to_be_visible();expect(self.el('batch-payment-1')).to_be_visible()
    def row(self,n=1,pid='p_z',amount='1.00',revision='1',at=temporal.T1):
        for name,value in [('payment',pid),('revision',revision),('amount',amount),('effective',at),('reason','  batch 정정 😀  ')]:self.el('batch-'+name+'-'+str(n)).fill(value)
    def intercept_loss(self,path):
        attempts=[];drop=[True]
        def intercept(route):
            req=route.request
            if req.method!='POST':return route.continue_()
            attempts.append((req.headers.get('idempotency-key'),req.post_data_json));response=route.fetch()
            if drop[0]:drop[0]=False;route.abort('failed')
            else:route.fulfill(response=response)
        self.page.route('**'+path,intercept);return attempts

    def test_refund_corrected_cap_original_receipt_and_success_repeat(self):
        token=self.token('ada');assert api.http('POST','/payments/p_z/corrections',{'expected_revision':1,'amount':120,'effective_at':temporal.T1,'reason':'correct'},token,'before')[0]==201
        self.receipt();original=self.el('original-receipt').inner_text();self.assertIn('2.00 EUR',original)
        expect(self.el('refund-corrected')).to_contain_text('1.20 EUR');expect(self.el('refund-remaining')).to_contain_text('1.20 EUR')
        self.el('refund-amount').fill('0.20');self.el('refund-submit').click();expect(self.el('refund-total')).to_contain_text('0.20 EUR')
        expect(self.el('refund-remaining')).to_contain_text('1.00 EUR');expect(self.el('original-receipt')).to_have_text(original)
        self.el('refund-submit').click();expect(self.el('refund-total')).to_contain_text('0.20 EUR')
        expect(self.el('refund-error')).to_have_count(0)

    def test_refund_decimal_validation_no_post(self):
        self.receipt();posts=[];self.page.on('request',lambda r:posts.append(r.url) if r.method=='POST' and r.url.endswith('/refunds') else None)
        for value in ('0','-1','0.001','abc'):
            self.el('refund-amount').fill(value);self.el('refund-submit').click();expect(self.el('refund-error')).to_be_visible()
        self.assertEqual(posts,[])

    def test_refund_lost_committed_response_exact_retry(self):
        self.receipt();self.el('refund-amount').fill('0.17');attempts=self.intercept_loss('/payments/p_z/refunds')
        self.el('refund-submit').click();expect(self.el('refund-uncertain')).to_be_visible();expect(self.el('refund-error')).to_have_count(0)
        expect(self.el('refund-amount')).to_have_value('0.17');self.el('refund-submit').click();expect(self.el('refund-total')).to_contain_text('0.17 EUR')
        expect(self.el('refund-uncertain')).to_have_count(0);self.assertEqual(len(attempts),2);self.assertEqual(attempts[0],attempts[1])

    def test_refund_competing_client_cap_refusal_preserves_input(self):
        self.receipt();self.el('refund-amount').fill('0.20')
        assert api.http('POST','/payments/p_z/refunds',{'amount':190},self.token('bob'),'other')[0]==201
        self.el('refund-submit').click();expect(self.el('refund-error')).to_be_visible();expect(self.el('refund-amount')).to_have_value('0.20')
        expect(self.el('refund-remaining')).to_contain_text('0.10 EUR')

    def test_batch_operator_manual_private_ids_no_revision_fetch(self):
        self.operations();reads=[];self.page.on('request',lambda r:reads.append(r.url) if r.method=='GET' and '/revisions' in r.url else None)
        self.row(pid='p_a',amount='0',at=temporal.T2);self.el('batch-submit').click();expect(self.el('batch-result-1')).to_be_visible()
        expect(self.el('batch-result-1')).to_contain_text('p_a');expect(self.el('batch-result-1')).to_contain_text('0.00 EUR');self.assertEqual(reads,[])
        self.assertEqual(api.http('GET','/payments/p_a/revisions',token=self.token('op'))[0],404)

    def test_batch_nonoperator_403_preserves_fields(self):
        self.page.goto(api.BASE+'/operations');expect(self.el('batch-role')).to_be_visible();self.row()
        self.el('batch-submit').click();expect(self.el('batch-error')).to_be_visible();expect(self.el('batch-payment-1')).to_have_value('p_z');expect(self.el('batch-amount-1')).to_have_value('1.00')

    def test_batch_rows_duplicate_decimal_offset_and_32_limit(self):
        self.operations();posts=[];self.page.on('request',lambda r:posts.append(r.url) if r.method=='POST' and r.url.endswith('/correction-batches') else None)
        self.row();self.el('batch-add').click();self.row(2);self.el('batch-submit').click();expect(self.el('batch-error')).to_be_visible();self.assertEqual(posts,[])
        self.el('batch-remove-2').click();expect(self.el('batch-payment-2')).to_have_count(0)
        for amount,at in [('0.001',temporal.T1),('1',temporal.T1[:-6])]:
            self.row(amount=amount,at=at);self.el('batch-submit').click();expect(self.el('batch-error')).to_be_visible()
        self.assertEqual(posts,[])
        for _ in range(31):self.el('batch-add').click()
        self.assertEqual(self.page.locator('[data-testid^="batch-payment-"]').count(),32)
        expect(self.el('batch-add')).to_be_disabled()

    def test_batch_order_offsets_and_unchanged_retry(self):
        self.operations();self.row(pid='p_m',amount='0.20',at='2020-01-01T04:00:00.0000000+02:00')
        self.el('batch-add').click();self.row(2,pid='p_z',amount='1.00');attempts=[]
        self.page.on('request',lambda r:attempts.append(r.post_data_json) if r.method=='POST' and r.url.endswith('/correction-batches') else None)
        self.el('batch-submit').click();expect(self.el('batch-result-1')).to_contain_text('p_m');expect(self.el('batch-result-2')).to_contain_text('p_z')
        expect(self.el('batch-recorded')).to_be_visible();self.assertEqual(attempts[0]['corrections'][0]['effective_at'],'2020-01-01T04:00:00.0000000+02:00')
        self.el('batch-submit').click();expect(self.el('batch-error')).to_have_count(0)
        rows=api.http('GET','/payments/p_z/revisions',token=self.token('ada'))[1]['revisions'];self.assertEqual(len(rows),2)

    def test_batch_lost_response_exact_retry_and_stale_input_preservation(self):
        self.operations();self.row();attempts=self.intercept_loss('/correction-batches')
        self.el('batch-submit').click();expect(self.el('batch-uncertain')).to_be_visible();expect(self.el('batch-error')).to_have_count(0)
        self.el('batch-submit').click();expect(self.el('batch-result-1')).to_be_visible();expect(self.el('batch-uncertain')).to_have_count(0)
        self.assertEqual(len(attempts),2);self.assertEqual(attempts[0],attempts[1])
        self.el('batch-amount-1').fill('0.90');self.el('batch-submit').click();expect(self.el('batch-error')).to_be_visible()
        expect(self.el('batch-amount-1')).to_have_value('0.90');expect(self.el('batch-revision-1')).to_have_value('1')

    def test_refund_and_operations_mobile_keyboard_contrast(self):
        metrics=[];colors=[]
        for width in (375,1280):
            self.page.set_viewport_size({'width':width,'height':900})
            for kind in ('refund','operations'):
                if kind=='refund':self.login('bob');self.receipt()
                else:self.operations();self.row()
                m=self.page.evaluate("""() => ({width:innerWidth,scroll:document.documentElement.scrollWidth,unlabelled:[...document.querySelectorAll('input,select,textarea')].filter(e=>e.getBoundingClientRect().width&&!e.labels?.length).map(e=>e.getAttribute('data-testid'))})""")
                self.assertEqual(m['scroll'],width);self.assertEqual(m['unlabelled'],[]);self.page.keyboard.press('Tab')
                focus=self.page.evaluate("""() => ({tag:document.activeElement.tagName,outline:getComputedStyle(document.activeElement).outlineStyle,width:getComputedStyle(document.activeElement).outlineWidth})""")
                self.assertNotEqual(focus['tag'],'BODY');self.assertNotEqual(focus['outline'],'none');metrics.append({'route':kind,**m,'focus':focus})
                self.page.screenshot(path=str(OUT/('visual-'+str(width)+'-'+kind+'.png')),full_page=True)
                palette=self.page.evaluate("""() => [...document.querySelectorAll('label,button,.subtext,[data-testid="batch-role"],[data-testid="refund-remaining"]')].filter(e=>e.getBoundingClientRect().width&&!e.disabled).map(e=>{let n=e,bg;while(n){bg=getComputedStyle(n).backgroundColor;if(bg!=='rgba(0, 0, 0, 0)'&&bg!=='transparent')break;n=n.parentElement;}return {fg:getComputedStyle(e).color,bg:bg||'rgb(255,255,255)'};})""")
                def luminance(c):
                    nums=[int(x)/255 for x in re.findall(r'\d+',c)[:3]];return sum((n/12.92 if n<=.04045 else ((n+.055)/1.055)**2.4)*w for n,w in zip(nums,[.2126,.7152,.0722]))
                for c in palette:
                    low,high=sorted((luminance(c['fg']),luminance(c['bg'])));c['ratio']=(high+.05)/(low+.05);self.assertGreaterEqual(c['ratio'],4.5)
                colors.extend(palette)
        (OUT/'layout.json').write_text(json.dumps(metrics,indent=2)+'\n');(OUT/'contrast.json').write_text(json.dumps({'pairs':len(colors),'minimum':min(c['ratio'] for c in colors)},indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--revision',required=True);p.add_argument('--out',required=True);p.add_argument('--browser-executable');p.add_argument('--insecure-host',action='store_true');a=p.parse_args()
    api.BASE=a.url.rstrip('/');previous.EXECUTABLE=a.browser_executable
    if a.insecure_host:
        with open('/etc/hosts','a') as hosts:hosts.write('\n127.0.0.1 pocketful-service\n')
        api.BASE=api.BASE.replace('127.0.0.1','pocketful-service');previous.INSECURE=True
    OUT=Path(a.out);OUT.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    with (OUT/'tests.log').open('w') as stream:r=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Stage4Browser))
    report={'revision':a.revision,'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'seconds':time.monotonic()-start,'failed_tests':[t.id() for t,_ in r.failures+r.errors]}
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));raise SystemExit(0 if r.wasSuccessful() and r.testsRun and not r.skipped else 1)
