#!/usr/bin/env python3
"""Dedicated data-repository release publisher. Requires explicit approved deployment.
Never run this against the private app repository. Existing releases are never replaced.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

REPOSITORY = 'ooishi1221/bus-countdown-data'

def publish(assets, repository):
    if repository != REPOSITORY:
        raise ValueError('Only the dedicated approved data repository is supported')
    m=json.loads((assets/'source-manifest.json').read_text())
    version=m['feed_version']
    if not re.fullmatch(r'\d{8}_\d{6}',version):
        raise ValueError('Invalid version')
    # A release with the same tag is immutable. Any command/network error aborts.
    releases=json.loads(subprocess.check_output(['gh','api',f'repos/{repository}/releases?per_page=100']))
    if any(r['tag_name']==version for r in releases):
        print('Version already published; expiry is unchanged')
        return
    latest = [r for r in releases if not r['draft'] and not r['prerelease']]
    if latest:
        old=max(latest,key=lambda r:r['tag_name'])
        if version <= old['tag_name']:
            raise ValueError('Refuse older version')
        # Do not re-date identical source bytes under a different release tag.
        old_manifest=json.loads(subprocess.check_output(['gh','release','download',old['tag_name'],
            '--repo',repository,'--pattern','source-manifest.json','--output','-']))
        if old_manifest['source_sha256']==m['source_sha256']:
            print('Source bytes unchanged; expiry is unchanged')
            return
    subprocess.run(['gh','release','create',version,'--repo',repository,'--draft',
        '--title','Toei '+version,'--notes-file',str(assets/'ATTRIBUTION.md'),
        *[str(assets/name) for name in ['schedule.sqlite','source-manifest.json','acquisition.json','ATTRIBUTION.md']]],check=True)
    subprocess.run(['gh','release','edit',version,'--repo',repository,'--draft=false','--latest'],check=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('assets',type=Path);p.add_argument('--repository',required=True)
    a=p.parse_args();publish(a.assets,a.repository)
