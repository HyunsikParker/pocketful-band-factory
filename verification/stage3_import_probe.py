"""Verify invalid imported history is rejected atomically, without logging state."""
import argparse,copy,json
from pathlib import Path
import stage1_acceptance as b
import stage3_api as s
p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--out',required=True);p.add_argument('--revision',required=True);a=p.parse_args()
out=Path(a.out);out.mkdir(parents=True,exist_ok=False);b.BASE=a.url
t=s.Stage3();t.setUp();t.reset(t.funding_fixture());before=t.snapshot();bad=copy.deepcopy(before)
bad['state']['payments']['p_aout']['created_at']=s.T1
bad['state']['revisions']['p_aout'][0].update(effective_at=s.T1,recorded_at=s.T1)
status,reply=b.http('POST','/_test/import',bad)
unchanged=t.snapshot()==before
after=t.me(as_of=s.T1)
report={'revision':a.revision,'import_status':status,'error_code':reply.get('error',{}).get('code') if isinstance(reply,dict) else None,'state_unchanged':unchanged,'historical_total_after':after['total'],'historical_available_after':after['available'],'expected':'422 validation_failed and unchanged destination; invalid history would debit Ada100 before funding exists'}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
assert status==422 and report['error_code']=='validation_failed' and unchanged
