"""
Детекторы моментов из командной строки — для отладки и подбора порогов.

Запуск (из корня проекта):
    python -m src.emotion_moments path/to/series.json
    S3_ENDPOINT=http://localhost:8333 python -m src.emotion_moments --session upload_ab12cd --profile interview
    python -m src.emotion_moments series.json --json        # полный результат в JSON
"""

import argparse
import json
import sys

from .moments import TYPE_TITLES
from .moments import detect_moments
from .overview import session_overview
from .profile import load_profile
from .series import EmotionSeries


def _load(args: argparse.Namespace) -> dict:
    if args.series:
        with open(args.series, encoding="utf-8") as f:
            return json.load(f)
    from src.object_storage import S3Settings
    from src.object_storage import S3Storage
    from src.session_archive import recording_keys

    storage = S3Storage(S3Settings.from_env())
    return json.loads(storage.get(recording_keys(args.session)["series"])["Body"].read())


def _mmss(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("series", nargs="?", help="путь к series.json")
    ap.add_argument("--session", help="id сессии: series.json из хранилища (S3_*)")
    ap.add_argument("--profile", default="interview", help="имя профиля или путь к YAML")
    ap.add_argument("--json", action="store_true", help="вывести обзор и моменты в JSON")
    args = ap.parse_args()
    if not args.series and not args.session:
        ap.error("нужен путь к series.json или --session")

    series   = EmotionSeries.from_document(_load(args))
    profile  = load_profile(args.profile)
    overview = session_overview(series)
    moments  = detect_moments(series, profile)
    if args.json:
        print(json.dumps({"overview": overview, "moments": [m.to_dict() for m in moments]}, ensure_ascii=False,
                         indent=1))
        return 0

    print(f"{series.camera_id}: {overview['duration_s']} с, лицо {overview['face_present']:.0%}, "
          f"речь {overview['speech']:.0%}, качество: {', '.join(overview['quality']) or 'норма'}")
    print(f"профиль {profile.name}: {len(moments)} моментов")
    for m in moments:
        kinds = ", ".join(TYPE_TITLES.get(t, t) for t in m.types)
        print(f"  {m.id:>4} {_mmss(m.start_s)}–{_mmss(m.end_s)} [{m.level:>6} {m.significance:.2f}] {kinds}")
        for c in m.candidates:
            print(f"       · {c.type}: {json.dumps(c.metrics, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
