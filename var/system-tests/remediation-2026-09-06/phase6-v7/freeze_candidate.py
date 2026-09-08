import hashlib,json,subprocess
from datetime import datetime,timezone
from pathlib import Path
from scripts.test_candidate import candidate_identity
root=Path('/wms/var/system-tests/remediation-2026-09-06')
load=lambda p:json.loads((root/p).read_text())
old=load('phase5/final-candidate-v6.json');source=load('phase5/candidate-source-v7.json')
assert candidate_identity('/wms')['source_digest']==source['source_digest']
prior=load('phase5/candidate-source-v6.json')
assert [p for p in sorted(set(prior['files'])|set(source['files'])) if prior['files'].get(p)!=source['files'].get(p)]==['docker/Dockerfile.test']
d={k:v for k,v in old.items() if k not in ('candidate_id','frozen_at')}
d.update(source_digest=source['source_digest'],runtime_source_digest=prior['source_digest'],test_image_id=subprocess.check_output(['docker','inspect','--format','{{.Id}}','wms-system-test:remediation-test-v7'],text=True).strip(),runtime_source_manifest='phase5/candidate-source-v6.json',acceptance_source_manifest='phase5/candidate-source-v7.json')
d['acceptance_driver_sha256']=hashlib.sha256((root/'phase6-v7/run_candidate_gate.py').read_bytes()).hexdigest()
d['reuse_policy_sha256']=hashlib.sha256((root/'phase6-v7/reuse-policy.json').read_bytes()).hexdigest()
d['test_build_inputs']={p:hashlib.sha256((Path('/tmp/wms-remediation-test-context-v7')/p).read_bytes()).hexdigest() for p in ('Dockerfile','test.txt','docker-cli','verify_runtime_dependencies.py')}
assert d['test_build_inputs']['Dockerfile']==source['files']['docker/Dockerfile.test']
d['mysql_client_source']='mysql:8.0.46@sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b'
d['runtime_configuration']['functional_database']='Reuse v6 clean real migration: exact runtime and model/migration files unchanged'
d['runtime_configuration']['functional_verification']='v6 full-run passed nodes plus v7 full preserved-data module rerun; original two tool failures retained'
d['candidate_id']=hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest()
d['frozen_at']=datetime.now(timezone.utc).isoformat()
with (root/'phase5/final-candidate-v7.json').open('x') as f:json.dump(d,f,indent=2)
print({'candidate_id':d['candidate_id'],'test_image_id':d['test_image_id'],'runtime_image_reused':d['runtime_image_id']})
