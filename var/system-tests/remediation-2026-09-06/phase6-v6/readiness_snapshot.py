"""Create an honest staged snapshot from individually selected evidence."""
import hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,'/wms')
from scripts.release_readiness import evaluate
root=Path('/wms/var/system-tests/remediation-2026-09-06')
candidate=json.loads((root/'phase5/final-candidate-v6.json').read_text())
policy=json.loads(Path('/wms/docs/release-gates.json').read_text())
now=datetime.now(timezone.utc).isoformat()
rows=[]
def add(gate,status,paths=(),**extra):
 artifacts=[{'path':p,'sha256':hashlib.sha256((root/p).read_bytes()).hexdigest()} for p in paths]
 rows.append({'gate_id':gate,'status':status,'candidate_id':candidate['candidate_id'],'finished_at':now,'artifacts':artifacts,**extra})
add('historical-evidence-review','PASS',['phase1/historical-review.json'])
add('runtime-smoke','PASS',['phase5/runtime-smoke-v6/runtime-smoke.json','phase5/runtime-django-check-v6.log','phase5/runtime-migration-drift-v6.log','phase5/source-verification-v6.log'],runtime_image_id=candidate['runtime_image_id'])
add('dependency-audit','BLOCKED',['phase2/pip-audit-69.json','phase2/npm-audit-final-summary.json','phase5/secret-scan-review-v6.json'],note='Production pip audit zero; npm high/critical zero, low/moderate findings remain registered; exact locks unchanged.')
add('image-security','BLOCKED',['phase5/image-security-dispositions-v6.json'],runtime_image_id=candidate['runtime_image_id'])
add('frontend','PASS',['phase5/frontend-summary-v6.json','phase3/frontend-final-v1/frontend-manifest.json','phase5/frontend-artifacts-v3.json'])
add('real-browser','PASS',['phase3/real-browser-final-v2.json','phase3/real-browser-final-v2.xml'],note='Host browser environment recorded; frontend inputs unchanged in candidate v6.')
add('pdf-resource-register','PASS',['phase2/pdf-measurements/measurement.json'],note='Risk remains open; measurement timeout is not a production control.')
add('external-gates-register','PASS',['phase4/external-open-items.json'])
for gate in policy['local']:
 if gate not in {r['gate_id'] for r in rows}:add(gate,'NOT_RUN',note='Final candidate gate not yet complete at this snapshot.')
for gate in policy['pre_release_additional']+policy['post_release_additional']:
 add(gate,'BLOCKED' if gate in ('pdf-resource-release-decision','capacity','disaster-recovery','wechat-payment','signed-apk-devices','manual-196','release-approval','staging-rehearsal-60m') else 'NOT_RUN',note='Actual environment, human decision or actual deployment evidence required; no local validator result substitutes for it.')
name=sys.argv[1];out=root/(name+'-gate-evidence.json')
assert not out.exists()
out.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
report=evaluate(candidate,rows,policy,root)
(root/(name+'-readiness.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
print({k:report[k] for k in ('local_complete','release_permitted','release_accepted')})
