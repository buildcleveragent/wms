"""Join audited functional revalidation with independently executed migration tests."""
import hashlib,json
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET
root=Path(__file__).parent
load=lambda n:json.loads((root/n).read_text())
functional=load('functional-reverification-review.json')
assert functional['status']=='PASS' and not functional['unresolved_failed_nodes']
migrations=load('migrations-tests.json');process=load('migrations-process.json')
assert migrations['status']=='PASS' and process['status']=='PASS' and process['cleanup_complete']
assert migrations['main_counts']=={'PASS':3} and not migrations['unexpected_nodes']
all_nodes={k:{'status':v['status']} for k,v in functional['accepted_nodes'].items()}
assert not set(all_nodes)&set(migrations['tests'])
all_nodes.update(migrations['tests'])
collected=load('collect-tests.json')
assert set(all_nodes)==set(collected['tests'])
raw=(root/'migrations.xml').read_bytes();assert b'<!DOCTYPE' not in raw.upper() and b'<!ENTITY' not in raw.upper()
xml=ET.fromstring(raw)
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
'cross_app_business_flows':'allapp/test_business_flows.py::',
'preserved_data_transfer':'allapp/core/test_preserved_data_transfer.py::'}.items():
 selected={k:v['status'] for k,v in all_nodes.items() if k.startswith(prefix)}
 matrix[label]={'status':'PASS' if selected and set(selected.values())=={'PASS'} else 'FAIL','count':len(selected),'nodes':selected}
result={'candidate_id':functional['candidate_id'],'kind':'ACCEPTANCE_AGGREGATE_WITH_EXPLICIT_REVERIFICATION','not_a_new_full_pytest_run':True,'main_counts':dict(Counter(t['status'] for t in all_nodes.values())),'stages':{'functional':{'status':functional['status'],'kind':functional['kind'],'baseline_main_counts':functional['baseline_main_counts'],'rerun_main_counts':functional['rerun_main_counts'],'subtest_counts':functional['subtests'],'raw_junit_counts':functional['junit_counts']},'migrations':{'status':migrations['status'],'process_status':process['status'],'main_counts':migrations['main_counts'],'subtest_counts':migrations['subtest_counts'],'cleanup_complete':process['cleanup_complete'],'duration_seconds':process['duration_seconds'],'junit_testcase_elements':len(list(xml.iter('testcase'))),'junit_summary_tests':sum(int(t.attrib.get('tests',0)) for t in xml.iter('testsuite'))}},'matrix':matrix,'collection_union_verified':True,'overlap':0,'limitations':['The failed v6 full run remains FAIL with its original XML unchanged.','v7 reruns the complete affected seven-test module and the three migration cases; other functional evidence is reused under the frozen policy.','945 historical aggregate-only subtests remain historically unidentified.']}
result['status']='PASS' if all(m['status']=='PASS' for m in matrix.values()) else 'FAIL'
result['artifacts']=[{'path':n,'sha256':hashlib.sha256((root/n).read_bytes()).hexdigest()} for n in ('functional-reverification-review.json','migrations-tests.json','migrations-process.json','migrations.xml','collect-tests.json','collection-reuse-review.json','reuse-policy.json')]
with (root/'final-results-review.json').open('x') as f:json.dump(result,f,indent=2)
print({k:v for k,v in result.items() if k not in ('matrix','artifacts')})
