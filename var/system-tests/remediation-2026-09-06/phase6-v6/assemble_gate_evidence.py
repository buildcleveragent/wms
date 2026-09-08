"""Select actual candidate evidence; unresolved gates remain blocked."""
import hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,'/wms')
from scripts.release_readiness import evaluate
root=Path('/wms/var/system-tests/remediation-2026-09-06')
load=lambda p:json.loads((root/p).read_text())
candidate=load('phase5/final-candidate-v6.json')
policy=json.loads(Path('/wms/docs/release-gates.json').read_text())
review=load('phase6-v6/final-results-review.json')
assert review['candidate_id']==candidate['candidate_id']
rows=[]
def add(gate,status,paths,**extra):
 artifacts=[{'path':p,'sha256':hashlib.sha256((root/p).read_bytes()).hexdigest()} for p in paths]
 finished=extra.pop('finished_at',datetime.now(timezone.utc).isoformat())
 rows.append({'gate_id':gate,'status':status,'candidate_id':candidate['candidate_id'],'finished_at':finished,'artifacts':artifacts,**extra})
def matrix_status(*keys):return 'PASS' if all(review['matrix'][k]['status']=='PASS' for k in keys) else 'FAIL'
review_path='phase6-v6/final-results-review.json'
add('test-coordination',matrix_status('coordination','process_cleanup'),[review_path,'phase6-v6/focused-tests.json'])
add('historical-evidence-review','PASS',['phase1/historical-review.json','phase5/evidence-reuse-v6.json'])
smoke=load('phase5/runtime-smoke-v6/runtime-smoke.json')
add('runtime-smoke',smoke['status'],['phase5/runtime-smoke-v6/runtime-smoke.json','phase5/runtime-django-check-v6.log','phase5/source-verification-v6.log'],runtime_image_id=candidate['runtime_image_id'])
audit_time=min((root/p).stat().st_mtime for p in ('phase2/pip-audit-69.json','phase2/npm-audit-final-summary.json'))
add('dependency-audit','BLOCKED',['phase2/pip-audit-69.json','phase2/npm-audit-final-summary.json','phase5/secret-scan-review-v6.json','phase5/evidence-reuse-v6.json'],finished_at=datetime.fromtimestamp(audit_time,timezone.utc).isoformat(),note='Production pip audit zero; npm high/critical zero, low/moderate findings remain registered. Older scan timestamps retained.')
security=load('phase5/image-security-dispositions-v6-reviewed.json')
add('image-security',security['status'],['phase5/trivy-final-v6.json','phase5/image-security-dispositions-v6-reviewed.json'],finished_at=datetime.fromtimestamp((root/'phase5/trivy-final-v6.json').stat().st_mtime,timezone.utc).isoformat(),runtime_image_id=candidate['runtime_image_id'])
add('business-matrix',matrix_status('movement_api','cross_app_business_flows'),[review_path,'phase6-v6/functional-tests.json'])
schema=load('phase6-v6/final-schema-verification.json')
schema_process=load('phase6-v6/final-schema-process.json')
assert schema_process['status']=='PASS' and schema_process['cleanup_complete']
add('migration-clean',schema['status'],['phase6-v6/final-schema-verification.json','phase6-v6/final-schema-process.json','phase6-v6/functional-process.json','phase5/runtime-migration-drift-v6.log'])
add('migration-nonempty-pos',matrix_status('pos_nonempty_upgrade','identifier_upgrade'),[review_path,'phase6-v6/migrations-tests.json','phase6-v6/migrations-process.json'])
add('backend-functional',review['stages']['functional']['status'],[review_path,'phase6-v6/functional-tests.json','phase6-v6/functional-process.json','phase6-v6/functional.xml'])
add('coverage',load('phase6-v6/coverage-gate.json')['status'],['phase6-v6/coverage-gate.json','phase6-v6/coverage.json'])
add('frontend','PASS',['phase5/frontend-summary-v6.json','phase3/frontend-final-v1/frontend-manifest.json','phase5/frontend-artifacts-v3.json','phase5/evidence-reuse-v6.json'])
add('real-browser','PASS',['phase3/real-browser-final-v2.json','phase3/real-browser-final-v2.xml','phase3/real-browser-screenshots/manifest.json','phase5/evidence-reuse-v6.json'],note='Real browser backend used host Python 3.12.3. Final backend authority is candidate-derived Python 3.12.14.')
add('pdf-acceptance',matrix_status('pdf_acceptance'),[review_path,'phase6-v6/functional-tests.json'])
add('pdf-resource-register','PASS',['phase2/pdf-measurements/measurement.json','phase6-v6/pdf-risk-register.json'],note='Risk is registered and still open; test timeout is not a production control.')
add('readiness-tool',matrix_status('readiness','evidence_recorder'),[review_path,'phase6-v6/focused-tests.json'])
add('external-gates-register','PASS',['phase4/external-open-items.json'])
for gate in policy['pre_release_additional']+policy['post_release_additional']:
 add(gate,'BLOCKED' if gate in policy['pre_release_additional'] else 'NOT_RUN',['phase4/external-open-items.json'],note='Actual independent environment, named human decision or deployment evidence is missing; local validators do not substitute for execution.')
assert {r['gate_id'] for r in rows}==set(policy['local']+policy['pre_release_additional']+policy['post_release_additional'])
name=sys.argv[1]
with (root/f'{name}-gate-evidence.json').open('x') as f:json.dump(rows,f,ensure_ascii=False,indent=2)
report=evaluate(candidate,rows,policy,root)
with (root/f'{name}-readiness.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
print({k:report[k] for k in ('local_complete','release_permitted','release_accepted')})
