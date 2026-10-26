#!/usr/bin/env python3
"""Reject non-Ubuntu OS/build paths before a costly or privileged ISO build."""
from pathlib import Path
import re
import sys
import yaml


def validate(root):
    workflow = yaml.safe_load((root / '.github/workflows/installer-iso.yml').read_text())
    base = workflow['env']['UBUNTU_LIVE_IMAGE']
    if not re.fullmatch(r'docker.io/library/ubuntu@sha256:[0-9a-f]{64}', base):
        raise ValueError('live/compiler image must be official digest-pinned Ubuntu')
    for file in ('Containerfile', 'build/ubuntu-live.Containerfile'):
        text = (root / file).read_text()
        for match in re.finditer(r'^FROM\s+(\S+)', text, re.M):
            allowed = {'scratch', 'base', '${UBUNTU_IMAGE}', '${PAYLOAD_IMAGE}', 'docker.io/library/ubuntu:${UBUNTU_TAG}'}
            if match[1] not in allowed:
                raise ValueError('non-Ubuntu build stage in ' + file + ': ' + match[1])
    for job in workflow['jobs'].values():
        if not job['runs-on'].startswith('ubuntu-'):
            raise ValueError('ISO workflow must run on Ubuntu')
    paths = [root / 'build/custom-installer-iso.sh', root / 'build/configure-live.sh',
             root / 'build/assemble-iso.sh', root / 'build/ubuntu-live.Containerfile',
             root / 'build/ubuntu-live-packages.py']
    text = '\n'.join(path.read_text() for path in paths)
    if re.search(r'\b(dnf5?|rpm(?:-ostree)?|yum)\b|titanoboa|build_files/build-live-iso.sh|bash\s+/?\S*installer/build.sh', text, re.I):
        raise ValueError('non-Ubuntu executable ISO assembly/toolchain path')
    if 'test "$ID" = ubuntu' not in (root / 'build/ubuntu-live.Containerfile').read_text():
        raise ValueError('missing runtime Ubuntu OS identity assertion')
    return base


if __name__ == '__main__':
    try:
        print('Ubuntu-only executable build guards passed: ' + validate(Path(__file__).resolve().parents[1]))
    except (KeyError, ValueError) as error:
        sys.exit(str(error))
