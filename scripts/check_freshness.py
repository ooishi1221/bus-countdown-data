#!/usr/bin/env python3
"""配布側の見張り。毎日の確認（verified.json）が3日以上止まっていたら失敗させる。

失敗した定期実行は GitHub の標準の通知（失敗したワークフローのメール／Web通知）で届く。
最新の公開版の manifest の fetched_at は、中身が変わらない日は古いままで正常なので使わない。
"""
import datetime as dt
import json
from pathlib import Path
import sys

MAX_AGE = dt.timedelta(days=3)
# 例外日（祝日など）の掲載が、この日数より先まで無くなったら知らせる。掲載が尽きると、アプリの表示期限もそこで止まる。
EXCEPTION_RUNWAY = dt.timedelta(days=7)


def problems(verified_path, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        verified = json.loads(Path(verified_path).read_text())
        checked = dt.datetime.fromtimestamp(float(verified['verified_at']), dt.timezone.utc)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [f'verified.json を読めません: {error!r}']
    found = []
    if verified.get('schema') != 1:
        found.append('verified.json の schema が想定と違います')
    if now - checked > MAX_AGE:
        found.append(f'公式データの確認が止まっています。最後の確認 {checked.isoformat()}（{(now - checked).days} 日前）')
    if checked - now > dt.timedelta(minutes=10):
        found.append(f'確認日時が未来です: {checked.isoformat()}')
    end = verified.get('calendar_dates_end')
    if end:
        try:
            last = dt.datetime.fromisoformat(end).replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
        except ValueError:
            found.append(f'calendar_dates_end が読めません: {end}')
        else:
            if last - now < EXCEPTION_RUNWAY:
                found.append(f'例外日（祝日など）の掲載が {end} までで、残り7日を切りました。公式が先の日付を載せるまで、アプリの表示期限もそこで止まります')
    return found


if __name__ == '__main__':
    found = problems(sys.argv[1])
    for line in found:
        print('::error::' + line)
    if found:
        sys.exit(1)
    print('OK: 公式データの確認は3日以内です')
