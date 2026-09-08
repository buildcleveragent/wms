import json,subprocess
from pathlib import Path
modules={}
for name in ('Archive/Tar.pm','File/GlobMapper.pm','Storable.pm'):
 matches=[]
 for directory in (Path('/usr/share/perl'),Path('/usr/lib'),Path('/usr/local/lib')):
  if directory.exists():
   matches.extend(str(p) for p in directory.rglob(name.split('/')[-1]) if str(p).endswith(name))
 modules[name]=matches
print(json.dumps({'perl_module_files':modules,'perl_include_paths':subprocess.check_output(['perl','-e','print join("\\n", @INC)'],text=True)},indent=2))
