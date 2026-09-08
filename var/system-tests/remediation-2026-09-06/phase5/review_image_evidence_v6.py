"""Attach bounded applicability evidence without changing the frozen image."""
import hashlib,json
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
root=Path(__file__).parent
d=json.loads((root/'image-security-dispositions-v6.json').read_text())
modules=json.loads((root/'optional-components-v6b.json').read_text())['perl_module_files']
absent={'CVE-2026-42496':'Archive/Tar.pm','CVE-2026-42497':'Archive/Tar.pm','CVE-2026-9538':'Archive/Tar.pm','CVE-2026-48962':'File/GlobMapper.pm','CVE-2026-57433':'Storable.pm'}
triggers={
'CVE-2025-69720':'Crafted terminal capabilities processed by infocmp analyze_string; infocmp exists in runtime.',
'CVE-2026-11822':'Malformed SQLite FTS5 database with MATCH query reaches memory corruption.',
'CVE-2026-11824':'Malformed SQLite FTS5 database with MATCH query reaches memory corruption.',
'CVE-2026-13221':'Perl regex with more than 65535 fixed branches can produce wrong match decisions.',
'CVE-2026-19487':'Perl regex alternation and stale Aho-Corasick failure state can miss an earlier match.',
'CVE-2026-41992':'Crafted LZW then LZH input in one gzip decompression invocation corrupts shared decoder state.',
'CVE-2026-54369':'Privileged path-based ACL operation races an attacker-controlled symbolic link.',
'CVE-2026-57432':'Perl pack/unpack attacker-controlled repeat count overflows and can read outside allocation.',
'CVE-2026-58010':'Malformed GVariant tuple padding causes a one-byte out-of-bounds read.',
'CVE-2026-58011':'Invalid GDateTime input reaches a two-byte out-of-bounds read.',
'CVE-2026-58012':'G_REGEX_RAW replacement with case conversion processes invalid UTF-8.',
'CVE-2026-58013':'GIOChannel custom multibyte line terminator reaches an out-of-bounds read.',
'CVE-2026-58014':'Untrusted key file with empty locale list value reaches g_key_file_get_locale_string_list.',
'CVE-2026-58015':'Malicious D-Bus server supplies traversing DBUS_COOKIE_SHA1 cookie_context.',
'CVE-2026-58016':'Malformed D-Bus introspection XML causes parser state confusion.',
'CVE-2026-76642':'Privileged mount runs ownership/idmap hooks after a failing mount helper.',
'CVE-2026-78408':'Privileged nsenter --join-cgroup inherits a root-opened cgroup.procs descriptor.',
'CVE-2026-78409':'Authorized X-mount.subdir entry and intermediate symlinks escape mounted tree on applicable kernel.',
'CVE-2026-78410':'Authorized bind source or ancestor can be replaced before privileged mount/ownership operation.',
'CVE-2026-86145':'Attacker-controlled regex, or recursive regex with a low heap limit, reaches pcre2_dfa_match.',
'TEMP-0000000-21C4F8':'Invalid UTF subject passed with PCRE2_MATCH_INVALID_UTF reaches out-of-bounds reads.',
'TEMP-0000000-8188AC':'Invalid UTF reaches pcre2_match after JIT fallback and causes an out-of-bounds read.'}
arch=json.loads((root/'runtime-components-v6.json').read_text())
assert any(p.startswith('libpcre2-8-0 amd64 ') for p in arch['packages'])
for r in d['rows']:
 key=r['id']
 if key in absent:
  module=absent[key]; assert modules[module]==[]
  r.update(disposition='NOT_APPLICABLE_COMPONENT',reason=f'{module} is absent from every actual Perl include directory in the original runtime image.',evidence=['optional-components-v6b.json'])
 elif key in ('TEMP-0000000-A5518C','TEMP-0000000-B05303'):
  r.update(disposition='NOT_APPLICABLE_ARCHITECTURE',reason='Official advisory is limited to 32-bit systems; runtime libpcre2-8-0 and libc6 are amd64.',evidence=['runtime-components-v6.json','runtime-smoke-v6/runtime-smoke.json'])
 if r['disposition']=='REVIEW_REQUIRED':
  assert key in triggers,key
  r.update(trigger=triggers[key],existing_controls='Runtime defaults to uid 10001. This limits privileges but does not prove the affected native path unreachable.',scope='Original candidate runtime; deployed capabilities, volumes and external services require separate evidence.',closure_condition='Supported fixed package and fresh image scan, or documented reachability assessment with security review and explicit release disposition.',release_disposition='BLOCKED_PENDING_REVIEW',owner='release-security (named assignee not yet supplied)')
  if key in ('CVE-2026-11822','CVE-2026-11824'):
   r['existing_controls']+=' WMS business/test DB is MySQL; this alone does not exclude every transitive SQLite consumer.'
  if key.startswith('CVE-2026-78') or key=='CVE-2026-76642':
   r['existing_controls']+=' Image /etc/fstab has no active entries; deployment mounts and operator commands remain unverified.'
  if key=='CVE-2026-86145':r['source']='https://github.com/PCRE2Project/pcre2/security/advisories/GHSA-3r4p-g7gg-ppmf'
  if key=='CVE-2026-19487':r['source']='https://github.com/Perl/perl5/issues/22892'
  if key=='CVE-2026-58014':r['source']='https://access.redhat.com/security/cve/cve-2026-58014'
for name in ('image-security-dispositions-v6.json','optional-components-v6b.json','optional-components-probe-v2.py','runtime-components-v6.json'):
 d['sources'][name]=hashlib.sha256((root/name).read_bytes()).hexdigest()
d['disposition_counts']=dict(Counter(r['disposition'] for r in d['rows']))
d['remaining_review_required']=d['unresolved']=d['disposition_counts']['REVIEW_REQUIRED']
d['reviewed_at']=datetime.now(timezone.utc).isoformat()
d['interpretation']='Raw package finding counts are unchanged; dispositions count distinct advisory IDs. No blanket severity suppression or production release approval.'
out=root/'image-security-dispositions-v6-reviewed.json'
with out.open('x') as f:json.dump(d,f,indent=2)
print(d['disposition_counts'])
