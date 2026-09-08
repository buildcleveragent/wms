import json,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,'/wms')
from scripts.test_process import run_managed
art=Path(__file__).parent
if len(sys.argv)>1:
 run_id=os.environ['WMS_TEST_RUN_ID']
 image=(art/'image-id.txt').read_text().strip()
 command=['docker','run','--rm','--init','--network=host','--user=0','--hostname',f'wms-runner-{run_id}','--label',f'com.wms.test-run={run_id}','--volume','/var/run/docker.sock:/var/run/docker.sock','--volume','/tmp/wms-test-coordination:/tmp/wms-test-coordination','--volume',f'{art}:/evidence','--env','WMS_TEST_MANIFEST=/evidence/focused-tests.json']
 for key in ('APP_ENV','SECRET_KEY','DB_ENGINE','DB_NAME','DB_TEST_NAME','DB_HOST','DB_PORT','DB_USER','DB_PASSWORD','REDIS_HOST','REDIS_PORT','WMS_TEST_RUN_ID'):
  if key in os.environ:command+=['--env',key]
 command+=[image,'python','-m','pytest','allapp/core/test_preserved_data_transfer.py::PreservedDataTransferMySQLTests','--reuse-db','-q','--tb=short','--junitxml=/evidence/focused.xml']
 raise SystemExit(subprocess.run(command,check=False).returncode)
previous=json.loads((art.parent/'phase6-v6/functional-process.json').read_text())
assert previous['status']!='RUNNING' and previous['cleanup_complete']
with (art/'focused.log').open('x') as log:
 r=run_managed([sys.executable,str(Path(__file__).resolve()),'child'],cwd=Path('/wms'),env=os.environ,stdout=log,record_path=art/'focused-process.json',timeout=300,docker_cleanup=True)
print({k:r.get(k) for k in ('status','exit_code','cleanup_complete','duration_seconds')})
raise SystemExit(0 if r['status']=='PASS' else 1)
