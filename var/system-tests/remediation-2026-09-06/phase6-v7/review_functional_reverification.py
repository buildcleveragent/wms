"""Accept individually evidenced nodes without rewriting the failed baseline run."""
import hashlib,json
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET
root=Path(__file__).parent.parent
load=lambda p:json.loads((root/p).read_text())
candidate=load('phase5/final-candidate-v7.json');prior_candidate=load('phase5/final-candidate-v6.json')
assert candidate['runtime_image_id']==prior_candidate['runtime_image_id']
old_sources=load('phase5/candidate-source-v6.json')['files'];new_sources=load('phase5/candidate-source-v7.json')['files']
assert [p for p in sorted(set(old_sources)|set(new_sources)) if old_sources.get(p)!=new_sources.get(p)]==['docker/Dockerfile.test']
environment=load('phase5/test-environment-v7-vs-v6.json')
assert environment['status']=='PASS' and environment['changed']==[]
policy=load('phase6-v7/reuse-policy.json')
old=load('phase6-v6/functional-tests.json');new=load('phase6-v7/focused-tests.json')
assert old['status']=='FAIL' and old['main_counts']=={'PASS':1248,'FAIL':2}
assert set(k for k,v in old['tests'].items() if v['status']=='FAIL')==set(policy['original_failure_nodes'])
assert new['status']=='PASS' and new['main_counts']=={'PASS':7}
assert not old['unexpected_nodes'] and not new['unexpected_nodes']
for p in ('phase6-v6/functional-process.json','phase6-v7/focused-process.json'):
 assert load(p)['cleanup_complete']
assert load('phase6-v7/focused-process.json')['status']=='PASS'
assert set(new['tests'])<=set(old['tests'])
for node in old['tests']:
 if node.startswith('allapp/core/test_preserved_data_transfer.py::'):assert node in new['tests']
accepted={}
for node,record in old['tests'].items():
 current=new['tests'].get(node,record)
 assert current['status']=='PASS',node
 accepted[node]={'status':'PASS','original_status':record['status'],'evidence':'phase6-v7/focused-tests.json' if node in new['tests'] else 'phase6-v6/functional-tests.json','verification':'RERUN' if node in new['tests'] else 'REUSED_UNCHANGED_INPUTS'}
events=[json.loads(s) for s in (root/'phase6-v6/functional-tests.events.jsonl').read_text().splitlines()]
children=[e for e in events if e.get('subtest_id')]
assert len(children)==945 and len({(e['run_id'],e['nodeid'],e.get('attempt'),e['subtest_id']) for e in children})==945
assert all(e['nodeid'] in old['tests'] and e['outcome']=='passed' for e in children)
xml_counts={}
for label,path in [('baseline','phase6-v6/functional.xml'),('rerun','phase6-v7/focused.xml')]:
 data=(root/path).read_bytes();assert b'<!DOCTYPE' not in data.upper() and b'<!ENTITY' not in data.upper()
 tree=ET.fromstring(data)
 xml_counts[label]={'testcase_elements':len(list(tree.iter('testcase'))),'framework_tests':sum(int(e.attrib.get('tests',0)) for e in tree.iter('testsuite'))}
result={'status':'PASS','candidate_id':candidate['candidate_id'],'kind':'FUNCTIONAL_ACCEPTANCE_BY_BASELINE_AND_SCOPED_REVERIFICATION','not_a_new_full_pytest_run':True,'baseline_run_id':old['run_id'],'baseline_main_counts':old['main_counts'],'rerun_run_id':new['run_id'],'rerun_main_counts':new['main_counts'],'accepted_main_count':len(accepted),'verification_counts':dict(Counter(r['verification'] for r in accepted.values())),'unresolved_failed_nodes':[],'subtests':{'passed':945,'unique_event_identities':945,'identity_scope':'run_id + parent nodeid + attempt + subtest_id','source':'phase6-v6/functional-tests.events.jsonl','historical_945_identities_restored':False},'junit_counts':xml_counts,'accepted_nodes':accepted}
for path in ('phase6-v6/functional-tests.json','phase6-v6/functional.xml','phase6-v6/functional-tests.events.jsonl','phase6-v7/focused-tests.json','phase6-v7/focused.xml','phase6-v7/reuse-policy.json','phase6-v7/cli-impact-search.txt','phase5/test-environment-v7-vs-v6.json','phase5/candidate-source-v6.json','phase5/candidate-source-v7.json'):
 result.setdefault('artifacts',[]).append({'path':path,'sha256':hashlib.sha256((root/path).read_bytes()).hexdigest()})
with (root/'phase6-v7/functional-reverification-review.json').open('x') as f:json.dump(result,f,indent=2)
print({k:v for k,v in result.items() if k not in ('accepted_nodes','artifacts')})
