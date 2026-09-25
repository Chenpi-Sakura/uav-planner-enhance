"""M5 联调验收脚本：按计划书 §9.2 的 3 航线 × 2 日期矩阵跑真实数据接口。

用法（需后端已在 9090 运行）：
    cd backend
    .python/portable/python.exe scripts/acceptance_m5.py [输出JSON路径]

输出：每条用例的可行性结论、风险、分段、替代方案与时间窗口关键字段，
全部用例打印 PASS/FAIL 汇总，便于答辩前快速复跑。
"""
import json
import os
import sys
import time
import urllib.request

# 默认打本机常驻实例；想在另一个端口上验新起的后端时用它覆盖，
# 免得为了跑一次验收去改这个脚本。
BASE = os.environ.get("UAV_API_BASE", "http://127.0.0.1:9090")

ROUTES = [
    ("拉萨-日喀则", (29.65, 91.13), (29.27, 88.88)),
    ("拉萨-林芝",   (29.65, 91.13), (29.66, 94.36)),
    ("昌都-拉萨",   (31.15, 97.18), (29.65, 91.13)),
]
DATES = ["2022-07-01", "2022-12-01"]


def post(path, payload, timeout=280):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()), time.time() - t0


def run():
    rows = []
    for name, s, e in ROUTES:
        for d in DATES:
            base = {
                "start": {"lat": s[0], "lon": s[1]},
                "end": {"lat": e[0], "lon": e[1]},
                "date": d, "start_time": "08:00", "speed": 60,
            }
            try:
                fe, t1 = post("/api/assess/feasibility", dict(base, priority="normal"))
                tw, t2 = post("/api/assess/time-window", dict(base, forecast_days=7))
            except Exception as exc:  # noqa: BLE001 - 验收脚本需报告任意失败
                rows.append({"route": name, "date": d, "error": repr(exc)[:200], "pass": False})
                continue

            risk = fe.get("risk") or {}
            dist = fe.get("distance") or {}
            sug = fe.get("suggestions") or {}
            rec = tw.get("recommended") or {}
            # 验收判据：接口可用 + 结论自洽 + 窗口输出项齐全
            checks = {
                "conclusion": fe.get("conclusion") in ("可飞", "不可飞", "超预算", "未算出"),
                "verdict_consistent": (fe.get("verdict") == "feasible") == bool(fe.get("feasible")),
                "reason_consistent": (fe.get("reason") is None) == bool(fe.get("feasible")),
                # 超预算有真实航线，route_km 是否给出只看 applicable，不看 feasible
                "distance_applicable": bool(dist.get("applicable")) == (dist.get("route_km") is not None),
                "tw_has_recommended": bool(rec.get("takeoff")) if tw.get("has_window") else True,
                "tw_slots": len(tw.get("slot_analysis") or []) > 0,
                "tw_meta_tz": (tw.get("meta") or {}).get("tz_offset_hours") == 8,
            }
            rows.append({
                "route": name, "date": d,
                "feasible": fe.get("feasible"), "conclusion": fe.get("conclusion"),
                "reason": fe.get("reason"),
                "route_km": dist.get("route_km"),
                "risk": risk.get("overall"),
                "segments": len(risk.get("segments") or []),
                "blocking": len((fe.get("blocking") or {}).get("regions") or []),
                "alt_bases": len(sug.get("alt_bases") or []),
                "alt_windows": len(sug.get("alt_windows") or []),
                "has_window": tw.get("has_window"),
                "takeoff": rec.get("takeoff"), "level": rec.get("level"),
                "backup_windows": len(tw.get("backup_windows") or []),
                "warnings": len(tw.get("warnings") or []),
                "curve_points": len((tw.get("curve") or {}).get("times") or []),
                "forecast_days": len(((tw.get("forecast") or {}).get("days") or [])),
                "t_feasibility_s": round(t1, 2), "t_time_window_s": round(t2, 2),
                "checks": checks, "pass": all(checks.values()),
            })
    return rows


def main():
    rows = run()
    text = json.dumps(rows, ensure_ascii=False, indent=1)
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
    passed = sum(1 for r in rows if r.get("pass"))
    print(f"\n===== M5 验收汇总：{passed}/{len(rows)} PASS =====")
    for r in rows:
        flag = "PASS" if r.get("pass") else "FAIL"
        detail = r.get("error") or (
            f"{r.get('conclusion')} / 窗口={r.get('has_window')} / {r.get('takeoff') or '-'}"
        )
        print(f"  [{flag}] {r['route']} {r['date']}  {detail}")
    sys.exit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()