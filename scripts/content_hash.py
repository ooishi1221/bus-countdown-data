#!/usr/bin/env python3
"""時刻表DBの「中身だけ」のハッシュ。

公式ZIPは毎日作り直されバイト列が変わっても、運行の中身（停留所・系統・便・運行日・例外日）が同じなら
同じ値になる。取得日時・feed_version・DB内部の連番（trips.id / options.id）は含めない。
公式が feed の終了日（feed_end）だけを延ばした場合も「変わった」と扱うため、feed_end は含める
（表示期限の上限に使うので、変わったらアプリにも新しい manifest を渡す必要がある）。
同じ値なら新しい版を出さず、確認日（verified_at）だけを更新する。
"""
import hashlib
import sqlite3
import sys

# feed_end をハッシュに含めるか。公式 ZIP の feed_start / feed_end が毎日ずれるなら False にする（1行で戻せる）。
# 変えたら、同梱 manifest の content_sha256 も scripts/rehash_bundled.py で計算し直す。公開済みの版の値は発行側が自動で計算し直す。
INCLUDE_FEED_END = True

QUERIES = [
    ('agency', 'SELECT id,name,timezone FROM agency ORDER BY id'),
    ('stops', 'SELECT id,name,parent,platform,kind,lat,lon FROM stops ORDER BY id'),
    ('routes', 'SELECT id,agency,name FROM routes ORDER BY id'),
    ('services', 'SELECT id FROM services ORDER BY id'),
    ('calendar', 'SELECT service,start,end,weekdays FROM calendar ORDER BY service'),
    ('exceptions', 'SELECT service,date,type FROM exceptions ORDER BY service,date'),
    ('trips', 'SELECT gtfs_id,route,service,direction,headsign,shape,description,pattern,origin FROM trips ORDER BY gtfs_id'),
    ('options', 'SELECT stop,route,direction,headsign,pattern,origin FROM options ORDER BY stop,route,direction,headsign,pattern'),
    ('stop_times', '''SELECT t.gtfs_id,s.seq,s.stop,s.arrival,s.departure,s.headsign,s.pickup,s.dropoff,s.terminal,
                      o.stop,o.route,o.direction,o.headsign,o.pattern
                      FROM stop_times s JOIN trips t ON t.id=s.trip LEFT JOIN options o ON o.id=s.option
                      ORDER BY t.gtfs_id,s.seq'''),
]


def content_sha256(path, feed_end):
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        digest = hashlib.sha256()
        if INCLUDE_FEED_END:
            digest.update(f'#feed_end {feed_end}\n'.encode())
        for name, sql in QUERIES:
            digest.update(f'#{name}\n'.encode())
            for row in db.execute(sql):
                digest.update(repr(row).encode())
                digest.update(b'\n')
        return digest.hexdigest()
    finally:
        db.close()


if __name__ == '__main__':
    print(content_sha256(sys.argv[1], sys.argv[2]))
