"""核心寻路算法 A/B 对跑：基线 230be8e vs main，同一份数据、同一批用例。

时间对齐：main 在查表前把北京时间换算成数据时间（UTC 标签，-8h），基线没有这一步。
为了让两边读到**完全相同**的气象场，基线侧直接喂换算后的数据时间。

输出只放结果、不放耗时（浮点用 repr 逐比特记录），便于 diff。
"""
import json
import os
import sys
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODE = sys.argv[1]
OUT = sys.argv[2]

UI_TZ_OFFSET_H = 8

CASES = [
    ("r_lhasa_shigatse", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.27, "lon": 88.88})),
    ("r_lhasa_nyingchi", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.66, "lon": 94.36})),
    ("r_via_nym", dict(start={"lat": 29.65, "lon": 91.13},
                       waypoints=[{"lat": 29.43, "lon": 90.16}], end={"lat": 29.27, "lon": 88.88})),
    ("r_dangxung_linzhou", dict(start={"lat": 30.48, "lon": 91.1}, end={"lat": 30.05, "lon": 91.26})),
    ("r_tight_thresh", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.27, "lon": 88.88},
                            tke_threshold=0.2, edr_threshold=0.05)),
    ("r_loose_thresh", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.27, "lon": 88.88},
                            tke_threshold=3.0, edr_threshold=1.0)),
    ("r_start_20h", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.27, "lon": 88.88},
                         start_time="20:00")),
    ("r_speed45", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.27, "lon": 88.88}, speed=45.0)),
    ("r_speed120", dict(start={"lat": 31.48, "lon": 92.05}, end={"lat": 31.21, "lon": 96.6}, speed=120.0)),
    ("r_qamdo_lhasa_winter", dict(start={"lat": 31.15, "lon": 97.18}, end={"lat": 29.65, "lon": 91.13},
                                  date="2022-12-01")),
    ("r_lhasa_shigatse_winter", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.27, "lon": 88.88},
                                     date="2022-12-01")),
]


def to_data_time(date_str, hhmm):
    """UI 北京时间 -> 数据时间标签（UTC，-8h），可能跨到前一天。"""
    dt = datetime.fromisoformat(f"{date_str}T{hhmm}:00") - timedelta(hours=UI_TZ_OFFSET_H)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def main():
    if MODE == "base":
        sys.path.insert(0, os.path.join(REPO, ".tmp-algo-audit-base"))
        from schemas import PlanRequest, Point  # noqa: F401
        from planner import plan_path
    else:
        sys.path.insert(0, os.path.join(REPO, "backend"))
        from schemas import PlanRequest, Point  # noqa: F401
        from planner import plan_path_ex

    out = {}
    for name, kw in CASES:
        date = kw.get("date", "2022-07-01")
        hhmm = kw.get("start_time", "08:00")
        req_kw = dict(kw)
        req_kw.pop("date", None)
        req_kw.pop("start_time", None)

        if MODE == "base":
            d, t = to_data_time(date, hhmm)
            req = PlanRequest(date=d, start_time=t, **req_kw)
            path, total_dist, max_tke, max_edr = plan_path(req)
            out[name] = {
                "requested_data_time": f"{d}T{t}",
                "total_distance": repr(float(total_dist)),
                "n_points": len(path),
                "path": [[repr(float(p.lat)), repr(float(p.lon))] for p in path],
            }
        else:
            req = PlanRequest(date=date, start_time=hhmm, **req_kw)
            res = plan_path_ex(req)
            d, t = to_data_time(date, hhmm)
            out[name] = {
                "requested_data_time": f"{d}T{t}",
                "found": bool(res.found),
                "reason": res.reason,
                "total_distance": repr(float(res.total_distance)),
                "n_points": len(res.path),
                "path": [[repr(float(p.lat)), repr(float(p.lon))] for p in res.path],
            }
        print(f"{name}: {out[name]['n_points']} pts", file=sys.stderr)

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
