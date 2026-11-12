#!/usr/bin/env python3
import os,hashlib,subprocess
from pathlib import Path
if os.geteuid()!=0:raise SystemExit('sudo required')
if Path('/sys/class/dmi/id/product_uuid').read_text().strip()!='3f614d56-7436-7a96-c8ea-a786bcc02265':raise SystemExit('VM identity mismatch')
source=Path('/home/zhang/m7-check-runner.py')
if hashlib.sha256(source.read_bytes()).hexdigest()!='eb421a75db4f77ecf3623ba640d86a8d89ce03ed5f1a7e21de73f22d1817d1f5':raise SystemExit('Runner checksum mismatch')
target=Path('/usr/local/sbin/mi2-m7-check');target.write_bytes(b'#!/usr/bin/python3\n'+source.read_bytes());target.chmod(0o755)
policy=Path('/etc/sudoers.d/94-mi2-m7-check');policy.write_text('zhang ALL=(root) NOPASSWD: /usr/local/sbin/mi2-m7-check check\n');policy.chmod(0o440)
subprocess.run(['/usr/sbin/visudo','-cf',str(policy)],check=True)
print('M7_FIXED_CHECK_READY')
