import hashlib,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
root=Path(__file__).parent
prior=root.parent/'phase6-v6'
policy=json.loads((root/'reuse-policy.json').read_text())
old=json.loads((prior/'functional-tests.json').read_text())
assert old['main_counts']=={'PASS':1248,'FAIL':2}
assert sorted(k for k,v in old['tests'].items() if v['status']=='FAIL')==sorted(policy['original_failure_nodes'])
assert old['subtest_counts']=={'passed':945}
assert json.loads((prior/'functional-process.json').read_text())['cleanup_complete']
coverage_source=prior/'.coverage.functional';coverage_target=root/'.coverage.functional-v6'
with coverage_target.open('xb') as f:f.write(coverage_source.read_bytes())
with (root/'coverage-reuse-input.json').open('x') as f:json.dump({'source':'phase6-v6/.coverage.functional','copy':'phase6-v7/.coverage.functional-v6','sha256':hashlib.sha256(coverage_source.read_bytes()).hexdigest(),'source_run_status':'FAIL: exactly two missing-client tests; closed only by later successful module rerun','measured_python_sources':'unchanged'},f,indent=2)
with (root/'controller.log').open('x',buffering=1) as output:
 for stage in ('focused','collect','migrations','coverage'):
  print('START',stage,datetime.now(timezone.utc).isoformat(),file=output,flush=True)
  r=subprocess.run([sys.executable,str(root/'run_candidate_gate.py'),stage],stdout=output,stderr=subprocess.STDOUT,check=False)
  print('END',stage,'exit',r.returncode,file=output,flush=True)
  if r.returncode:raise SystemExit(r.returncode)
  if stage=='focused':
   current=json.loads((root/'focused-tests.json').read_text())
   assert current['status']=='PASS' and all(k in current['tests'] and current['tests'][k]['status']=='PASS' for k in policy['original_failure_nodes'])
  if stage=='collect':
   current=json.loads((root/'collect-tests.json').read_text())
   initial=json.loads((prior/'collect-tests.json').read_text())
   assert set(current['tests'])==set(initial['tests']) and current['status']=='COLLECTED'
   with (root/'collection-reuse-review.json').open('x') as f:json.dump({'status':'PASS','same_node_ids':True,'collected':len(current['tests']),'previous':'phase6-v6/collect-tests.json'},f,indent=2)
