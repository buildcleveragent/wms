import json,subprocess
from pathlib import Path
roots=subprocess.check_output(['perl','-e','print join(chr(0), @INC)']).decode().split('\0')
assert all(Path(p).is_absolute() for p in roots)
modules={name:[str(Path(root)/name) for root in roots if (Path(root)/name).is_file()] for name in ('Archive/Tar.pm','File/GlobMapper.pm','Storable.pm')}
print(json.dumps({'perl_module_files':modules,'perl_include_paths':roots,'scope':'all actual runtime Perl include directories'},indent=2))
