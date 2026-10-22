#!/usr/bin/env python3
"""Dedicated data-repository release publisher. Requires explicit approved deployment.
Never run this against the private app repository. Existing releases are never replaced.

公式データの中身（content_sha256）が最新の公開版と同じ日は、新しい版を出さない。
どちらの場合も --verified-out に、確認結果（verified.json）を書く。アプリはこれを見て、
DBを取り直さずに表示期限だけを延ばす。
"""
import argparse
import gzip
import json
from pathlib import Path
import re
import subprocess
import tempfile

from content_hash import content_sha256

REPOSITORY = 'ooishi1221/bus-countdown-data'
ASSETS = ['schedule.sqlite.gz', 'source-manifest.json', 'acquisition.json', 'ATTRIBUTION.md']
# 自動公開の歯止め: 前の版からこれらの件数が10%を超えて減ったら公開しない（公式データの欠落・変換の不具合の疑い）。
GATED_COUNTS = ['stops', 'trips', 'stop_times', 'options']
MINIMUM_RATIO = 0.9


def check_not_shrunk(new, old, ratio=MINIMUM_RATIO):
    for name in GATED_COUNTS:
        before, after = old.get('counts', {}).get(name), new['counts'][name]
        if before and after < before * ratio:
            raise ValueError(f'{name} dropped from {before} to {after} (below {ratio:.0%}); not publishing')


def tag_content_hash(repository, tag, feed_end):
    """古い版の manifest に content_sha256 が無い時だけ、その版のDBから計算する。"""
    with tempfile.TemporaryDirectory() as temp:
        subprocess.run(['gh', 'release', 'download', tag, '--repo', repository, '--pattern', 'schedule.sqlite*',
                        '--dir', temp], check=True)
        folder = Path(temp)
        compressed = folder / 'schedule.sqlite.gz'
        if compressed.exists():
            (folder / 'schedule.sqlite').write_bytes(gzip.decompress(compressed.read_bytes()))
        return content_sha256(folder / 'schedule.sqlite', feed_end)


def write_verified(path, manifest, release):
    """アプリが読む確認結果。DBは含めず、中身のハッシュと確認日時だけ。"""
    Path(path).write_text(json.dumps(dict(schema=1, feed_version=release, content_sha256=manifest['content_sha256'],
        verified_at=manifest['fetched_at'], calendar_dates_end=manifest.get('calendar_dates_end'),
        release=release), indent=2) + '\n')


def publish(assets, repository, verified_out=None, allow_shrink=False):
    if repository != REPOSITORY:
        raise ValueError('Only the dedicated approved data repository is supported')
    m = json.loads((assets / 'source-manifest.json').read_text())
    version = m['feed_version']
    if not re.fullmatch(r'\d{8}_\d{6}', version):
        raise ValueError('Invalid version')
    if not re.fullmatch(r'[a-f0-9]{64}', m.get('content_sha256', '')):
        raise ValueError('Missing content hash')
    # Any command/network error aborts. Nothing is replaced.
    releases = json.loads(subprocess.check_output(['gh', 'api', f'repos/{repository}/releases?per_page=100']))
    latest = [r for r in releases if not r['draft'] and not r['prerelease']]
    if latest:
        old = max(latest, key=lambda r: r['tag_name'])
        old_manifest = json.loads(subprocess.check_output(['gh', 'release', 'download', old['tag_name'],
            '--repo', repository, '--pattern', 'source-manifest.json', '--output', '-']))
        old_content = old_manifest.get('content_sha256') or tag_content_hash(repository, old['tag_name'], old_manifest['feed_end'])
        if old_content == m['content_sha256']:
            # 公式ZIPのバイト列は毎日変わっても、中身が同じなら新しい版は出さない。
            print('Content unchanged; no new release. Verification date only.')
            if verified_out:
                write_verified(verified_out, m, old['tag_name'])
            return old['tag_name']
        if version <= old['tag_name']:
            raise ValueError('Refuse older version')
        if allow_shrink:
            # 人が確認したうえでの上書き（workflow_dispatch の allow_shrink）。対象は GATED_COUNTS の件数の歯止めだけ。
            print('allow_shrink: the 90% count gate is skipped for this run')
        else:
            check_not_shrunk(m, old_manifest)
    for stale in [r for r in releases if r['tag_name'] == version]:
        if not stale['draft']:
            raise ValueError('Same version tag is already published with different content; inspect manually')
        # 前回の公開が途中で止まった残り（公開されていない下書き）だけは、消して作り直す。
        subprocess.run(['gh', 'release', 'delete', version, '--repo', repository, '--yes'], check=True)
    subprocess.run(['gh', 'release', 'create', version, '--repo', repository, '--draft',
        '--title', 'Toei ' + version, '--notes-file', str(assets / 'ATTRIBUTION.md'),
        *[str(assets / name) for name in ASSETS]], check=True)
    subprocess.run(['gh', 'release', 'edit', version, '--repo', repository, '--draft=false', '--latest'], check=True)
    if verified_out:
        write_verified(verified_out, m, version)
    return version


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('assets', type=Path)
    p.add_argument('--repository', required=True)
    p.add_argument('--verified-out', type=Path)
    p.add_argument('--allow-shrink', action='store_true', help='skip the 90%% count gate (stops/trips/stop_times/options only)')
    a = p.parse_args()
    publish(a.assets, a.repository, a.verified_out, a.allow_shrink)
