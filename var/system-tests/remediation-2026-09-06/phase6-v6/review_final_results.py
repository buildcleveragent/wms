"""Audit completed candidate results without inventing missing child identities."""
import hashlib,json,sys
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET
root=Path(__file__).parent
stages={}
all_nodes={}
for stage in ('functional','migrations'):
 path=root/f'{stage}-tests.json'
 if not path.exists():raise SystemExit(f'{stage} final manifest is not available')
 d=json.loads(path.read_text()); process=json.loads((root/f'{stage}-process.json').read_text())
 xmlraw=(root/f'{stage}.xml').read_bytes()
 if b'<!DOCTYPE' in xmlraw.upper() or b'<!ENTITY' in xmlraw.upper():raise SystemExit('Unsafe JUnit XML')
 tree=ET.fromstring(xmlraw)
 overlap=set(all_nodes)&set(d['tests'])
 if overlap:raise SystemExit('Overlapping test groups')
 all_nodes.update(d['tests'])
 stages[stage]={'status':d['status'],'process_status':process['status'],'cleanup_complete':process['cleanup_complete'],'duration_seconds':process['duration_seconds'],'main_counts':d['main_counts'],'subtest_counts':d['subtest_counts'],'junit_testcase_elements':len(list(tree.iter('testcase'))),'junit_summary_tests':sum(int(t.attrib.get('tests',0)) for t in tree.iter('testsuite')),'unexpected_nodes':d['unexpected_nodes'],'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
collected=json.loads((root/'collect-tests.json').read_text())
assert set(all_nodes)==set(collected['tests']),'Final group union differs from collection'
matrix={}
for label,prefix in {
'movement_api':'allapp/tasking/test_movement_api_acceptance.py::',
'pdf_acceptance':'allapp/reports/test_pdf_acceptance.py::',
'coordination':'allapp/core/test_test_coordination.py::',
'process_cleanup':'allapp/core/test_test_process.py::',
'evidence_recorder':'allapp/core/test_test_evidence.py::',
'readiness':'allapp/core/test_release_readiness.py::',
'pos_nonempty_upgrade':'allapp/pos/test_repayment_migrations.py::',
'identifier_upgrade':'allapp/products/test_identifier_migrations.py::',
'cross_app_business_flows':'allapp/test_business_flows.py::'}.items():
 selected={k:v['status'] for k,v in all_nodes.items() if k.startswith(prefix)}
 matrix[label]={'status':'PASS' if selected and set(selected.values())=={'PASS'} else 'FAIL','count':len(selected),'nodes':selected}
result={'candidate_id':json.loads((root.parent/'phase5/final-candidate-v6.json').read_text())['candidate_id'],'stages':stages,'main_counts':dict(Counter(t['status'] for t in all_nodes.values())),'matrix':matrix,'collection_union_verified':True,'overlap':0,'limitations':['Historic 945 aggregate-only subtests remain historically unidentified.','Focused smoke results overlap the functional suite and are not added to the main total.']}
result['status']='PASS' if all(x['status']=='PASS' and x['process_status']=='PASS' and x['cleanup_complete'] for x in stages.values()) and all(m['status']=='PASS' for m in matrix.values()) else 'FAIL'
with (root/'final-results-review.json').open('x') as f:json.dump(result,f,indent=2)
print(json.dumps({k:v for k,v in result.items() if k!='matrix'},indent=2))
