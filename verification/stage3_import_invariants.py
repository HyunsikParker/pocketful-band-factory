"""Independent malformed temporal import invariants and valid snapshot roundtrip."""
import argparse,copy,json,time,unittest
from pathlib import Path
import stage1_acceptance as base
import stage3_api as temporal

class Imports(unittest.TestCase):
    def setUp(self):self.api=temporal.Stage3();self.api.setUp()
    def rejected(self,before,invalid):
        self.api.error(422,'validation_failed','POST','/_test/import',invalid,who=None)
        # Do not emit exported state/token values in unittest diffs.
        self.assertTrue(self.api.snapshot()==before,'invalid import changed destination')
    def test_total_boundary_precedes_funding(self):
        self.api.reset(self.api.funding_fixture());before=self.api.snapshot();bad=copy.deepcopy(before)
        bad['state']['payments']['p_aout']['created_at']=temporal.T1
        bad['state']['revisions']['p_aout'][0].update(effective_at=temporal.T1,recorded_at=temporal.T1)
        self.rejected(before,bad)
    def test_historical_available_negative_but_current_voided(self):
        self.api.reset(base.fixture());hold=self.api.authorize();self.api.good('POST','/authorizations/'+hold['authorization_id']+'/void',{},status=200)
        before=self.api.snapshot();bad=copy.deepcopy(before);aid=hold['authorization_id']
        bad['state']['authorizations'][aid]['amount']=150
        bad['state']['authorization_lifecycle'][aid][0]['remaining_amount']=150
        self.rejected(before,bad)
    def test_unchanged_corrected_export_snapshot_and_receipts(self):
        old=self.api.statement();revision=self.api.correction(amount=100,key='receipt')
        current=self.api.statement();before=self.api.snapshot()
        self.assertEqual(base.http('POST','/_test/import',before)[0],204)
        self.assertEqual(self.api.statement(snapshot=old['snapshot']),old)
        self.assertEqual(self.api.statement(snapshot=current['snapshot']),current)
        body={'expected_revision':1,'amount':100,'effective_at':temporal.T1,'reason':'correction'}
        self.assertEqual(self.api.good('POST','/payments/p_z/corrections',body,key='receipt',status=200),revision)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--revision',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    base.BASE=a.url;out=Path(a.out);out.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    with (out/'tests.log').open('w') as stream:r=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Imports))
    report={'revision':a.revision,'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'seconds':time.monotonic()-start,'failed_tests':[t.id() for t,_ in r.failures+r.errors]}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));raise SystemExit(0 if r.wasSuccessful() and r.testsRun and not r.skipped else 1)
