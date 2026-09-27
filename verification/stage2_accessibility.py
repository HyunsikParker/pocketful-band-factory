"""Independent actual-browser keyboard, contrast and maximum mobile-balance audit."""
import argparse,json,re
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
import stage1_acceptance as api
from stage2_browser import ui_fixture

def ratio(fg,bg):
    def luminance(c):
        rgb=[int(x)/255 for x in re.findall(r'\d+',c)[:3]]
        a=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
        return a[0]*.2126+a[1]*.7152+a[2]*.0722
    x,y=sorted((luminance(fg),luminance(bg)))
    return (y+.05)/(x+.05)

p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--revision',required=True);p.add_argument('--out',required=True)
a=p.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=False);api.BASE=a.url
data=ui_fixture('BHD',3);data['users'][0]['balance']=2**53
assert api.http('POST','/_test/reset',data)[0]==204
with sync_playwright() as pw:
    browser=pw.chromium.launch();page=browser.new_page(viewport={'width':375,'height':900});errors=[];external=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:external.append(True) if not r.url.startswith(a.url+'/') else None)
    page.goto(a.url+'/login');page.get_by_test_id('login-email').fill('ada@example.test');page.get_by_test_id('login-password').fill(api.PASSWORD);page.get_by_test_id('login-submit').click()
    expect(page.get_by_test_id('wallet-available')).to_have_text('9007199254740.992 BHD')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_test_id('pay-handle').focus();page.keyboard.press('Tab')
    focus=page.evaluate('({id:document.activeElement.dataset.testid,outline:getComputedStyle(document.activeElement).outlineWidth,style:getComputedStyle(document.activeElement).outlineStyle})')
    assert focus['id']=='pay-amount' and float(focus['outline'].replace('px',''))>=2 and focus['style']!='none'
    page.screenshot(path=str(out/'375-focus-max-balance.png'),full_page=True)
    selectors=['body','.subtext','.field label','.field small','.primary','.secondary','.balance-label','.balance-value','.balance-meta dd','.card-kicker','.nav a']
    colors=page.evaluate('''selectors => selectors.map(selector=>{const e=document.querySelector(selector);const s=getComputedStyle(e);let n=e,bg;while(n){bg=getComputedStyle(n).backgroundColor;if(bg!=='rgba(0, 0, 0, 0)'&&bg!=='transparent')break;n=n.parentElement;}return {selector,fg:s.color,bg:bg||'rgb(255,255,255)'};})''',selectors)
    for c in colors:c['contrast']=ratio(c['fg'],c['bg']);assert c['contrast']>=4.5,c
    report={'revision':a.revision,'focus':focus,'contrast':colors,'external_requests':len(external),'page_errors':errors,'passed':not errors and not external,'viewport':375}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));browser.close()
assert report['passed']
