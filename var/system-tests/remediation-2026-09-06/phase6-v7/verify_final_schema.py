"""Read-only final schema and migration-drift checks with bounded cleanup."""
import json,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,'/wms')
from scripts.test_process import run_managed
root=Path('/wms/var/system-tests/remediation-2026-09-06')
art=root/'phase6-v7'
candidate=json.loads((root/'phase5/final-candidate-v7.json').read_text())
if len(sys.argv)>1 and sys.argv[1]=='child':
 run_id=os.environ['WMS_TEST_RUN_ID']
 common=['docker','run','--rm','--init','--network=host','--hostname',f'wms-runner-{run_id}','--label',f'com.wms.test-run={run_id}']
 for key in ('APP_ENV','SECRET_KEY','DB_ENGINE','DB_NAME','DB_TEST_NAME','DB_HOST','DB_PORT','DB_USER','DB_PASSWORD','REDIS_HOST','REDIS_PORT','WMS_TEST_RUN_ID'):
  if key in os.environ:common+=['--env',key]
 code='import json; from pathlib import Path; from scripts.run_system_tests import preflight; d=preflight(schema=True); d.update(status="PASS",validation="Real migration HEAD, consistent history and all managed model tables/columns"); Path("/evidence/final-schema-verification.json").write_text(json.dumps(d,indent=2)); print(json.dumps(d))'
 command=common+['--user=0','--volume','/var/run/docker.sock:/var/run/docker.sock','--volume','/tmp/wms-test-coordination:/tmp/wms-test-coordination','--volume',f'{art}:/evidence',candidate['test_image_id'],'python','-c',code]
 subprocess.run(command,check=True)
 with (root/'phase5/runtime-migration-drift-v7.log').open('x') as log:
  checked=subprocess.run(common+[candidate['runtime_image_id'],'python','manage.py','makemigrations','--check','--dry-run'],stdout=log,stderr=subprocess.STDOUT,check=False)
 if checked.returncode:raise SystemExit(checked.returncode)
 print('Original runtime migration-drift check passed',flush=True)
 raise SystemExit(0)
for stage in ('focused','migrations'):
 assert json.loads((art/f'{stage}-process.json').read_text())['status']=='PASS','Run after successful test groups'
with (art/'final-schema-process.log').open('x') as log:
 result=run_managed([sys.executable,str(Path(__file__).resolve()),'child'],cwd=Path('/wms'),env=os.environ,stdout=log,record_path=art/'final-schema-process.json',timeout=300,docker_cleanup=True)
print({k:result.get(k) for k in ('status','exit_code','cleanup_complete','duration_seconds')})
raise SystemExit(0 if result['status']=='PASS' else 1)
