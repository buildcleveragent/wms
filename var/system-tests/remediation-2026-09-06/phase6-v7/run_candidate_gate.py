"""Validate the changed test tool layer, preserving immutable runtime and prior evidence."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path('/wms')
sys.path.insert(0,str(ROOT))
from scripts.test_process import run_managed
from scripts.test_candidate import candidate_identity
BASE=ROOT/'var/system-tests/remediation-2026-09-06'
ART=BASE/'phase6-v7'
SOURCE=BASE/'phase5/candidate-source-v6.json'
ACCEPTANCE=BASE/'phase5/candidate-source-v7.json'
IDENTITY=BASE/'phase5/final-candidate-v7.json'
candidate=json.loads(IDENTITY.read_text())
assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==candidate['acceptance_driver_sha256']
stage=sys.argv[1]
assert stage in ('collect','focused','migrations','coverage')
if len(sys.argv)>2 and sys.argv[2]=='child':
 run_id=os.environ['WMS_TEST_RUN_ID']
 command=['docker','run','--rm','--init','--network=host','--user=0','--hostname',f'wms-runner-{run_id}','--name',f'wms-candidate-{stage}-{run_id[:12]}','--label',f'com.wms.test-run={run_id}','--label','com.wms.purpose=candidate-acceptance','--volume','/var/run/docker.sock:/var/run/docker.sock','--volume','/tmp/wms-test-coordination:/tmp/wms-test-coordination','--volume',f'{ART}:/evidence','--volume',f'{SOURCE}:/runtime-source.json:ro']
 for key in ('APP_ENV','SECRET_KEY','DB_ENGINE','DB_NAME','DB_TEST_NAME','DB_HOST','DB_PORT','DB_USER','DB_PASSWORD','REDIS_HOST','REDIS_PORT','WMS_TEST_RUN_ID'):
  if key in os.environ:command+=['--env',key]
 command+=['--env',f'WMS_TEST_MANIFEST=/evidence/{stage}-tests.json','--env',f'COVERAGE_FILE=/evidence/.coverage.{stage}']
 if stage=='coverage':
  code='import subprocess; from pathlib import Path; import json; from scripts.check_coverage_gate import check; subprocess.run(["python","-m","coverage","combine","--keep","--data-file=/evidence/.coverage","/evidence"],check=True); subprocess.run(["python","-m","coverage","json","--data-file=/evidence/.coverage","-o","/evidence/coverage.json"],check=True); d=check(json.load(open("/evidence/coverage.json")),json.load(open("docs/release-gates.json"))); Path("/evidence/coverage-gate.json").write_text(json.dumps(d,indent=2)); print(d); raise SystemExit(0 if d["status"]=="PASS" else 1)'
  command+=[candidate['test_image_id'],'python','-c',code]
 else:
  code='import json,sys; from scripts.test_candidate import verify_candidate; d=verify_candidate("/app",json.load(open("/runtime-source.json"))); print("VERIFIED_RUNTIME_SOURCE",d["source_digest"],flush=True); import pytest; raise SystemExit(pytest.main(sys.argv[1:]))'
  command+=[candidate['test_image_id'],'python','-c',code,'--reuse-db','-q','--tb=short','--durations=10',f'--junitxml=/evidence/{stage}.xml']
  if stage=='collect':command+=['--collect-only','allapp']
  elif stage=='focused':command+=['allapp/core/test_preserved_data_transfer.py','--cov=allapp','--cov-report=','--cov-fail-under=0']
  else:command+=['allapp','--wms-group=migrations','--cov=allapp','--cov-report=','--cov-fail-under=0']
 raise SystemExit(subprocess.run(command,check=False).returncode)
assert candidate_identity(ROOT)['source_digest']==json.loads(ACCEPTANCE.read_text())['source_digest'],'Acceptance inputs changed'
assert json.loads(SOURCE.read_text())['source_digest']==candidate['runtime_source_digest']
with (ART/f'{stage}.log').open('x') as output:
 result=run_managed([sys.executable,str(Path(__file__).resolve()),stage,'child'],cwd=ROOT,env=os.environ,stdout=output,record_path=ART/f'{stage}-process.json',timeout=7500,docker_cleanup=True)
print(json.dumps({k:result.get(k) for k in ('status','exit_code','reason','cleanup_complete','duration_seconds')}),flush=True)
raise SystemExit(0 if result['status']=='PASS' else 1)
