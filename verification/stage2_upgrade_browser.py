"""Open-browser S1->S2 upgrade, preserving real legacy token and lost-write identity.

Stage2 serves static UI; intercepted API calls initially use actual stage1.
No DOM/localStorage credential injection, reload or re-login during the upgrade.
"""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect
import stage1_acceptance as api
from stage2_browser import ui_fixture


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-url',required=True)
    parser.add_argument('--target-url',required=True)
    parser.add_argument('--source-revision',required=True)
    parser.add_argument('--target-revision',required=True)
    parser.add_argument('--out',required=True)
    parser.add_argument('--browser-executable')
    parser.add_argument('--source-stop-dir',required=True,help='Orchestrator handshake directory; source removal must complete before import')
    parser.add_argument('--insecure-host',action='store_true',help='Disposable Docker runner only')
    args=parser.parse_args()
    out=Path(args.out)
    out.mkdir(parents=True,exist_ok=False)
    source=args.source_url.rstrip('/')
    target=args.target_url.rstrip('/')
    if args.insecure_host:
        with open('/etc/hosts','a') as hosts:hosts.write('\n127.0.0.1 pocketful-service\n')
        target=target.replace('127.0.0.1','pocketful-service')
    api.BASE=source
    assert api.http('POST','/_test/reset',ui_fixture())[0]==204
    status,login=api.http('POST','/auth/login',{'email':'bob@example.test','password':api.PASSWORD})
    assert status==200
    status,pending=api.http('POST','/requests',{'payer_handle':'ada','amount':700,'note':'before upgrade'},token=login['token'],key='legacy-pending')
    assert status==201
    api.BASE=target
    assert api.http('POST','/_test/reset',ui_fixture('BHD',3))[0]==204
    stage={'backend':source,'lost':False}
    attempts=[]
    navigations=[]
    checks=[]
    started=time.monotonic()
    with sync_playwright() as pw:
        options={'headless':True}
        if args.browser_executable:
            options['executable_path']=args.browser_executable
        browser=pw.chromium.launch(**options)
        context=browser.new_context(viewport={'width':1280,'height':900})
        page=context.new_page()
        page.set_default_timeout(5000)
        page.on('framenavigated',lambda f:navigations.append(f.url) if f==page.main_frame else None)
        def route_api(route):
            request=route.request
            url=urlsplit(request.url)
            path=url.path
            is_api=(path in ('/me','/activity','/payments','/splits','/settlements') or path.startswith('/auth/') or path.startswith('/requests') or path.startswith('/authorizations'))
            if not is_api or request.is_navigation_request():
                return route.continue_()
            endpoint=stage['backend']+path+('?' + url.query if url.query else '')
            response=route.fetch(url=endpoint)
            if path=='/payments' and request.method=='POST':
                attempts.append({'key':request.headers.get('idempotency-key'),'body':request.post_data_json,'status':response.status,'backend':stage['backend']})
                if not stage['lost']:
                    assert stage['backend']==source and response.status==201
                    stage['lost']=True
                    return route.abort('failed')
            route.fulfill(response=response)
        page.route('**/*',route_api)
        try:
            page.goto(target+'/login')
            capability=page.evaluate('({origin:location.origin,secure:isSecureContext,randomUUID:typeof crypto.randomUUID})')
            if args.insecure_host:assert not capability['secure'] and capability['randomUUID']=='undefined'
            page.get_by_test_id('login-email').fill('ada@example.test')
            page.get_by_test_id('login-password').fill(api.PASSWORD)
            page.get_by_test_id('login-submit').click()
            expect(page.get_by_test_id('current-user')).to_contain_text('Ada')
            page.goto(target+'/')
            expect(page.get_by_test_id('wallet-balance')).to_have_text('100.00 EUR')
            page.get_by_test_id('pay-handle').fill('bob')
            page.get_by_test_id('pay-amount').fill('1.25')
            page.get_by_test_id('pay-note').fill('unchanged through upgrade')
            page.get_by_test_id('pay-visibility').select_option('private')
            page.get_by_test_id('pay-submit').click()
            expect(page.get_by_test_id('pay-uncertain')).to_be_visible()
            expect(page.get_by_test_id('pay-error')).to_have_count(0)
            before=len(navigations)
            api.BASE=source
            status,exported=api.http('GET','/_test/export')
            assert status==200
            stop=Path(args.source_stop_dir)
            stop.mkdir(parents=True,exist_ok=False)
            (stop/'ready').write_text('export-in-memory; remove source container\n')
            deadline=time.monotonic()+30
            while not (stop/'removed').exists():
                if time.monotonic()>deadline:raise RuntimeError('Source container removal was not confirmed')
                time.sleep(.1)
            checks.append('source-container-removed-before-import')
            api.BASE=target
            assert api.http('POST','/_test/import',exported)[0]==204
            stage['backend']=target
            assert len(navigations)==before
            expect(page.get_by_test_id('pay-note')).to_have_value('unchanged through upgrade')
            page.get_by_test_id('pay-submit').click()
            expect(page.get_by_test_id('wallet-balance')).to_have_attribute('data-amount','9875')
            expect(page.get_by_test_id('wallet-available')).to_have_attribute('data-amount','9875')
            expect(page.get_by_test_id('pay-uncertain')).to_have_count(0)
            expect(page.get_by_test_id('pay-error')).to_have_count(0)
            assert len(attempts)==2
            assert attempts[0]['key']==attempts[1]['key'] and attempts[0]['body']==attempts[1]['body']
            assert [a['status'] for a in attempts]==[201,200]
            assert [a['backend'] for a in attempts]==[source,target]
            checks.extend(['legacy-login-token-preserved','no-reload-at-import','same-payment-key-and-body','original-payment-replayed-200','imported-balance-refreshed'])
            page.goto(target+'/requests')
            rid=pending['request_id']
            expect(page.get_by_test_id('request-pay-'+rid)).to_be_visible()
            page.get_by_test_id('request-pay-'+rid).click()
            expect(page.get_by_test_id('request-item-'+rid)).to_have_attribute('data-status','paid')
            page.goto(target+'/')
            expect(page.get_by_test_id('wallet-balance')).to_have_attribute('data-amount','9175')
            expect(page.get_by_test_id('current-user')).to_contain_text('Ada')
            checks.append('legacy-pending-request-paid-in-upgraded-browser')
            page.screenshot(path=str(out/'upgraded-wallet.png'),full_page=True)
            report={'source_revision':args.source_revision,'target_revision':args.target_revision,'checks':checks,'passed':True,'seconds':time.monotonic()-started,'payment_statuses':[a['status'] for a in attempts],'capability':capability}
            (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report))
        except Exception as error:
            page.screenshot(path=str(out/'failure.png'),full_page=True)
            (out/'report.json').write_text(json.dumps({'source_revision':args.source_revision,'target_revision':args.target_revision,'passed':False,'completed_checks':checks,'error_type':type(error).__name__,'error':str(error),'seconds':time.monotonic()-started},indent=2)+'\n')
            raise
        finally:
            context.close()
            browser.close()


if __name__=='__main__':
    main()
