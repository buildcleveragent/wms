"""Read bounded test progress without SQL text or credentials."""
import os,json
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import MySQLdb
root=Path(__file__).parent
result={'at':datetime.now(timezone.utc).isoformat()}
for stage in ('functional','migrations'):
 events=root/f'{stage}-tests.events.jsonl'
 if events.exists():
  rows=[]
  for line in events.read_text().splitlines():
   try:rows.append(json.loads(line))
   except json.JSONDecodeError:pass
  result[stage]={'event_count':len(rows),'event_kinds':dict(Counter(x['kind'] for x in rows)),'last_time':rows[-1]['time'] if rows else None}
  reports=[x for x in rows if x['kind']=='report']
  result[stage]['main_call_counts']=dict(Counter(x['outcome'] for x in reports if x.get('phase')=='call' and not x.get('subtest_id')))
  result[stage]['subtest_counts']=dict(Counter(x['outcome'] for x in reports if x.get('subtest_id')))
  result[stage]['failed_reports']=[{k:x.get(k) for k in ('nodeid','phase','outcome','subtest_id')} for x in reports if x['outcome']=='failed']
  if reports:result[stage]['last_report']={k:v for k,v in reports[-1].items() if k in ('nodeid','outcome','phase')}
assert os.environ['DB_NAME']=='wms_db_test'
c=MySQLdb.connect(host=os.environ['DB_HOST'],port=int(os.environ['DB_PORT']),user='root',passwd=os.environ['MYSQL_ROOT_PASSWORD'],db='wms_db_test',connect_timeout=5,read_timeout=5,autocommit=True)
try:
 with c.cursor() as q:
  q.execute('SELECT DATABASE(),VERSION()');result['mysql_identity']=q.fetchone()
  q.execute('SELECT COUNT(*) FROM django_migrations');result['migration_count']=q.fetchone()[0]
  q.execute('SELECT app,name FROM django_migrations ORDER BY id DESC LIMIT 3');result['latest_migrations']=q.fetchall()
  q.execute("SELECT COMMAND,TIME,STATE FROM information_schema.PROCESSLIST WHERE DB='wms_db_test' AND ID<>CONNECTION_ID()")
  result['connection_states_without_sql']=q.fetchall()
finally:c.close()
with (root/'live-progress.jsonl').open('a') as f:f.write(json.dumps(result)+'\n')
print(json.dumps(result))
