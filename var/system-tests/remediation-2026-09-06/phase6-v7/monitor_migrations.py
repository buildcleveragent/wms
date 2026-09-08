"""Bounded read-only progress for this run's disposable MySQL instances."""
import json,subprocess
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import MySQLdb
root=Path(__file__).parent
process=json.loads((root/'migrations-process.json').read_text())
run_id=process['run_id']
result={'at':datetime.now(timezone.utc).isoformat(),'process_status':process['status']}
rows=[json.loads(s) for s in (root/'migrations-tests.events.jsonl').read_text().splitlines()]
reports=[r for r in rows if r['kind']=='report']
result['main_call_counts']=dict(Counter(r['outcome'] for r in reports if r.get('phase')=='call' and not r.get('subtest_id')))
result['failed_reports']=[{k:r.get(k) for k in ('nodeid','phase','outcome')} for r in reports if r['outcome']=='failed']
ids=subprocess.check_output(['docker','ps','--filter',f'label=com.wms.test-run={run_id}','--filter','label=com.wms.purpose=isolated-migration-test','--format','{{.ID}}'],text=True,timeout=10).split()
result['isolated_instances']=[]
for identifier in ids:
 item=json.loads(subprocess.check_output(['docker','inspect',identifier],text=True,timeout=10))[0]
 env=dict(s.split('=',1) for s in item['Config']['Env'] if '=' in s)
 assert item['Config']['Labels']['com.wms.test-run']==run_id
 assert item['Name'].startswith('/wms-test-migration-') and env['MYSQL_DATABASE']=='wms_db_test'
 port=item['NetworkSettings']['Ports']['3306/tcp'][0]
 assert port['HostIp']=='127.0.0.1'
 c=MySQLdb.connect(host='127.0.0.1',port=int(port['HostPort']),user='root',passwd=env['MYSQL_ROOT_PASSWORD'],db='wms_db_test',connect_timeout=5,read_timeout=5,autocommit=True)
 try:
  with c.cursor() as q:
   q.execute('SELECT DATABASE(),VERSION()');identity=q.fetchone();assert identity[0]=='wms_db_test' and identity[1].startswith('8.')
   q.execute('SELECT COUNT(*) FROM django_migrations');count=q.fetchone()[0]
   q.execute('SELECT app,name FROM django_migrations ORDER BY id DESC LIMIT 2');latest=q.fetchall()
   q.execute("SELECT name FROM django_migrations WHERE app='pos' ORDER BY id");pos_migrations=[r[0] for r in q.fetchall()]
   result['isolated_instances'].append({'name':item['Name'],'identity':identity,'migration_count':count,'latest':latest,'pos_latest':pos_migrations[-1] if pos_migrations else None})
 finally:c.close()
with (root/'migration-progress.jsonl').open('a') as f:f.write(json.dumps(result)+'\n')
print(json.dumps(result))
