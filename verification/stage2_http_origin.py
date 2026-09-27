"""Reproduce actual application writes at a non-secure HTTP origin, using local transport only."""
import argparse, json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect
import stage1_acceptance as api
from stage2_browser import ui_fixture

p=argparse.ArgumentParser()
p.add_argument('--url',required=True)
p.add_argument('--revision',required=True)
p.add_argument('--out',required=True)
a=p.parse_args()
out=Path(a.out); out.mkdir(parents=True,exist_ok=False)
api.BASE=a.url
assert api.http('POST','/_test/reset',ui_fixture())[0]==204
with sync_playwright() as pw:
    browser=pw.chromium.launch()
    page=browser.new_page()
    page.set_default_timeout(5000)
    errors=[]; writes=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    def relay(route):
        req=route.request; u=urlsplit(req.url)
        if req.method=='POST' and u.path=='/payments': writes.append(True)
        response=route.fetch(url=a.url+u.path+('?' + u.query if u.query else ''))
        route.fulfill(response=response)
    page.route('**/*',relay)
    page.goto('http://pocketful-service:8080/login')
    page.get_by_test_id('login-email').fill('ada@example.test')
    page.get_by_test_id('login-password').fill(api.PASSWORD)
    page.get_by_test_id('login-submit').click()
    expect(page.get_by_test_id('wallet-balance')).to_have_text('100.00 EUR')
    capability=page.evaluate('({origin:location.origin,secure:isSecureContext,randomUUID:typeof crypto.randomUUID})')
    page.get_by_test_id('pay-handle').fill('bob')
    page.get_by_test_id('pay-amount').fill('1.00')
    page.get_by_test_id('pay-submit').click()
    page.wait_for_timeout(250)
    report={'revision':a.revision,'capability':capability,'page_errors':errors,'payment_posts':len(writes),'balance':page.get_by_test_id('wallet-balance').inner_text(),'pay_error_count':page.get_by_test_id('pay-error').count(),'passed':len(writes)==1 and not errors}
    page.screenshot(path=str(out/'http-origin.png'),full_page=True)
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    browser.close()
raise SystemExit(0 if report['passed'] else 1)
