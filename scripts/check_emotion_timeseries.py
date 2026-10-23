"""
Диагностика emotion_timeseries после прогона пайплайна; в БД ничего не пишет.

Запуск:
    python scripts/check_emotion_timeseries.py
    python scripts/check_emotion_timeseries.py --user a3f...
    python scripts/check_emotion_timeseries.py --limit 3 --samples
"""

import argparse
import json
import sys
import psycopg2

from collections import Counter
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List

from psycopg2.extras import RealDictCursor


DB = {
    "host": "localhost",
    "port": 5433,
    "database": "emotions",
    "user": "app_user",
    "password": "basic_app_password",
}


def fmt_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    except Exception:
        return "—"


def fmt_dur(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def summarize_row(row: Dict[str, Any], show_samples: bool) -> None:
    data: Dict[str, Any] = row["data"]
    samples: List[Dict[str, Any]] = data.get("samples") or []
    started  = data.get("started_at") or 0.0
    ended    = data.get("ended_at") or started
    duration = max(0.0, ended - started)

    labels        = Counter(s.get("label") for s in samples if s.get("label"))
    dominant      = labels.most_common(1)[0][0] if labels else "—"
    label_summary = ", ".join(f"{k}={v}" for k, v in labels.most_common())

    print(
        f"  user={row['user_id']}  "
        f"camera={data.get('camera_id', '—')}  "
        f"track={data.get('track_id', '—')}  "
        f"track_dur={fmt_dur(duration)}  "
        f"samples={len(samples)}  "
        f"dominant={dominant}"
    )
    if label_summary:
        print(f"      labels: {label_summary}")
    if show_samples and samples:
        print("      first 3 samples:")
        for s in samples[:3]:
            scores = s.get("scores") or []
            top    = max(scores) if scores else 0
            print(
                f"        t={fmt_ts(s.get('t', 0))}  label={s.get('label')}  top_score={top:.2f}"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", help="Фильтр по конкретному UUID пользователя.")
    ap.add_argument(
        "--limit", type=int, default=10, help="Сколько последних записей показать (default 10)."
    )
    ap.add_argument("--samples", action="store_true", help="Показать первые сэмплы каждой записи.")
    args = ap.parse_args()

    try:
        conn = psycopg2.connect(**DB)
    except Exception as e:
        print(f"Cannot connect to {DB['host']}:{DB['port']}/{DB['database']} — {e}", file=sys.stderr)
        return 1

    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT
            COUNT(*) AS rows,
            COUNT(DISTINCT user_id) AS users,
            COALESCE(SUM(jsonb_array_length(data->'samples')), 0) AS total_samples
        FROM emotion_timeseries
        """
    )
    agg = cur.fetchone() or {}
    print("== emotion_timeseries ==")
    print(f"  rows         : {agg.get('rows', 0)}")
    print(f"  unique users : {agg.get('users', 0)}")
    print(f"  total samples: {agg.get('total_samples', 0)}")
    print()

    if (agg.get("rows") or 0) == 0:
        print("Таблица пустая. Запусти пайплайн на видео и подожди, пока трек уйдёт.")
        cur.close()
        conn.close()
        return 0

    if args.user:
        cur.execute(
            """
            SELECT user_id, data, created_at
            FROM emotion_timeseries
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (args.user, args.limit),
        )
    else:
        cur.execute(
            """
            SELECT user_id, data, created_at
            FROM emotion_timeseries
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (args.limit,),
        )

    rows = cur.fetchall()
    print(f"== last {len(rows)} {'rows for user ' + args.user if args.user else 'rows'} ==")
    for row in rows:
        print(f"\n[{row['created_at']}]")
        summarize_row(row, show_samples=args.samples)

    print()

    cur.execute(
        """
        SELECT s->>'label' AS label, COUNT(*) AS cnt
        FROM emotion_timeseries,
             LATERAL jsonb_array_elements(data->'samples') AS s
        GROUP BY label
        ORDER BY cnt DESC
        """
    )
    print("== dominant emotions across all samples ==")
    for r in cur.fetchall():
        print(f"  {r['label'] or '—'}: {r['cnt']}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
