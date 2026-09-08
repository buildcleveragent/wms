import json,os,platform,sys,subprocess,shutil
from pathlib import Path
import MySQLdb,weasyprint
from weasyprint import HTML
assert os.getuid()==10001
assert os.environ['DB_NAME']=='wms_db_test'
connection=MySQLdb.connect(host=os.environ['DB_HOST'],port=int(os.environ['DB_PORT']),user=os.environ['DB_USER'],passwd=os.environ['DB_PASSWORD'],db=os.environ['DB_NAME'],connect_timeout=5,charset='utf8mb4')
with connection.cursor() as cursor:
 cursor.execute('SELECT DATABASE(), VERSION(), @@character_set_connection')
 identity=cursor.fetchone()
connection.close()
assert identity[0]=='wms_db_test' and identity[1].startswith('8.')
out=Path('/output')
HTML(string='<html><meta charset="utf-8"><style>body{font-family:"Noto Sans CJK SC"}</style><h1>仓库出库单</h1><p>合成商品 数量 4 金额 ¥38.00</p></html>').write_pdf(out/'runtime-chinese.pdf')
facts={'machine':platform.machine(),'uid':os.getuid(),'python':sys.version.split()[0],'weasyprint':weasyprint.__version__,'mysql_identity':identity,'mysql_client':MySQLdb.get_client_info(),'systemd_homed_present':any(Path(p).exists() for p in ['/usr/lib/systemd/systemd-homed','/lib/systemd/systemd-homed']),'mariadbd':shutil.which('mariadbd'),'mbstream':shutil.which('mbstream'),'perl_word_size':subprocess.check_output(['perl','-V:ivsize','-V:ptrsize'],text=True),'pdf_bytes':(out/'runtime-chinese.pdf').stat().st_size,'status':'PASS'}
(out/'runtime-smoke.json').write_text(json.dumps(facts,indent=2));print(json.dumps(facts))
