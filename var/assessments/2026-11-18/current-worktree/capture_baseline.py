import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
root = Path('/wms')
def git(*args):
    return subprocess.check_output(['git', *args], cwd=root)
def filelist(*args):
    return set(os.fsdecode(s) for s in git(*args, '-z').split(b'\0') if s)
tracked = filelist('diff','--name-only','--no-renames','HEAD')
untracked = filelist('ls-files','--others','--exclude-standard')
digest = hashlib.sha256()
sha = git('rev-parse','HEAD').decode().strip()
digest.update(sha.encode() + b'\0')
included = []
for name in sorted(tracked | untracked):
    if name.startswith('var/assessments/'):
        continue
    f = root / name
    if f.is_symlink():
        mode, content = 'symlink', os.fsencode(os.readlink(f))
    elif f.is_file():
        mode, content = oct(f.stat().st_mode & 0o777), f.read_bytes()
    else:
        mode, content = 'absent', b''
    digest.update(os.fsencode(name) + b'\0' + mode.encode() + b'\0' + hashlib.sha256(content).digest())
    included.append(name)
report = dict(observed_utc=datetime.now(timezone.utc).isoformat(),commit=sha,branch=git('branch','--show-current').decode().strip(), digest=digest.hexdigest(),tracked_delta=len(tracked.intersection(included)),untracked=len(untracked.intersection(included)),algorithm='SHA256 of HEAD and sorted git changed/untracked nonignored paths, each with NUL-delimited path, file permission or symlink/absent and SHA256 of working content. Excludes var/assessments/ (audit outputs).',paths=included)
(root/'var/assessments/2026-11-18/current-worktree'/sys.argv[1]).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(sha,report['digest'],report['tracked_delta'],report['untracked'])
