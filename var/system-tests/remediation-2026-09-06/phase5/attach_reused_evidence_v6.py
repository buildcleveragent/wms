import hashlib,json
from pathlib import Path
from datetime import datetime,timezone
root=Path(__file__).parent
load=lambda n:json.loads((root/n).read_text())
old,new=load('candidate-source-v5.json'),load('candidate-source-v6.json')
changed=[k for k in sorted(set(old['files'])|set(new['files'])) if old['files'].get(k)!=new['files'].get(k)]
assert changed==['allapp/core/test_test_evidence.py','wmsmaster/test_evidence.py']
checks={
 'frontend':'Frontend source, locks, build configuration and previously hashed H5 output unchanged; reuse actual 251 logic/26 intercepted-browser/7 real-flow results.',
 'historical-evidence-review':'Historical raw artifacts are immutable; no retroactive restoration of missing subtest identities.',
 'python-dependency-audit':'Production lock and image installed dependency set unchanged; retain the actual earlier scan timestamp.',
 'pdf-resource-measurement':'PDF implementation/dependencies unchanged; host measurement is budget input only, not a production limit or candidate-runtime benchmark.',
 'secret-scan':'Prior candidate source scan reused; only evidence recorder and its tests changed, reviewed diff contains no credential addition. This is not a fresh full secret scan.'}
d={'candidate_id':load('final-candidate-v6.json')['candidate_id'],'from_source_digest':old['source_digest'],'to_source_digest':new['source_digest'],'changed_inputs':changed,'reuse_decisions':checks,'created_at':datetime.now(timezone.utc).isoformat(),'limitations':['Backend functional/migration authority must come from v6 derived image.', 'Real browser server used host Python 3.12.3.', 'Evidence helper outputs do not mutate frozen acceptance inputs.']}
for filename in ('candidate-source-v5.json','candidate-source-v6.json','frontend-summary-v5.json','secret-scan-review-v5.json','frontend-artifacts-v3.json'):
 d.setdefault('artifacts',[]).append({'path':filename,'sha256':hashlib.sha256((root/filename).read_bytes()).hexdigest()})
with (root/'evidence-reuse-v6.json').open('x') as f:json.dump(d,f,indent=2)
front=load('frontend-summary-v5.json');front.update(candidate_id=d['candidate_id'],reuse_evidence='evidence-reuse-v6.json')
with (root/'frontend-summary-v6.json').open('x') as f:json.dump(front,f,indent=2)
secret=load('secret-scan-review-v5.json');secret.update(candidate_id=d['candidate_id'],reuse_evidence='evidence-reuse-v6.json',scope=checks['secret-scan'])
with (root/'secret-scan-review-v6.json').open('x') as f:json.dump(secret,f,indent=2)
print({'changed_inputs':changed,'evidence_reuse':'RECORDED'})
