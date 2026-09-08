import hashlib,json,subprocess
from datetime import datetime,timezone
from pathlib import Path
root=Path('/wms');out=root/'var/system-tests/remediation-2026-09-06';p=out/'phase5/final-candidate-v6.json'
assert not p.exists()
source=json.loads((out/'phase5/candidate-source-v6.json').read_text())
identity=lambda tag: subprocess.check_output(['docker','image','inspect',tag,'--format','{{.Id}}'],text=True).strip()
components={'source_digest':source['source_digest'],'revision':source['revision'],'runtime_image_id':identity('wms-system-test:remediation-candidate-v6'),'test_image_id':identity('wms-system-test:remediation-test-v6'),'acceptance_driver_sha256':hashlib.sha256((out/'phase6-v6/run_candidate_gate.py').read_bytes()).hexdigest(),'frontend_artifacts_sha256':hashlib.sha256((out/'phase5/frontend-artifacts-v3.json').read_bytes()).hexdigest(),'runtime_configuration':{'python':'3.12.14','node':'22.23.2','npm':'10.9.8','mysql':'8.0.46','database':'wms_db_test','APP_ENV':'test','coordination':'/tmp/wms-test-coordination shared single-host; unique runner hostname; default per-container PID namespace','password_hashing':'configured default; no fast-password-hashers','functional_database':'clean real migration once','migration_group':'independent disposable MySQL instances'},'build_args':{'DEBIAN_MIRROR':'https://mirrors.tuna.tsinghua.edu.cn','PIP_INDEX_URL':'https://pypi.tuna.tsinghua.edu.cn/simple'},'docker_cli_sha256':hashlib.sha256(Path('/usr/bin/docker').read_bytes()).hexdigest()}
components['candidate_id']=hashlib.sha256(json.dumps(components,sort_keys=True).encode()).hexdigest()
components['frozen_at']=datetime.now(timezone.utc).isoformat()
p.write_text(json.dumps(components,indent=2));print(components['candidate_id'])
