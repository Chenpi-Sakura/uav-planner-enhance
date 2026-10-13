"""公网服务可用性巡检与自动修复。

为什么需要它：本机是单点，睡眠、断网、Windows Update 都会让公网服务下线，
而 `start.bat` 是给人双击的（结尾有 `pause`，且 `start_watcher.ps1` 盯着它的窗口，
窗口一消失就把后端+frpc 整棵进程树杀掉），不能被脚本无头调用。所以这里做一条
独立于 start.bat 的巡检链路。

三层探活，逐层收窄（只看状态码会漏判：Cloudflare 出错时会回 502/530，
也可能回一个带错误页的 200）：

    ① 公网  https://uav.caipiischenpi.top/api/meta/cities
    ② 本机  http://127.0.0.1:<config.json 端口>/api/meta/cities
    ③ 本机  http://127.0.0.1:<端口>/            （dist 挂载是否还在）

第 ③ 层专防 2026-10-11 那次「API 全绿、整站 404」——`/` 的静态挂载只在 main.py
被 import 时判断一次，dist 缺失就会一直 404，而 API 看起来完全健康。

修复原则是「只重启真正挂掉的那一层」，并且先分清三件事：本机出口网络是不是断了、
隧道是不是挂了、以及**后端是不是只是在预载气象数据**。

最后一件事最要命。**只有全量那一份**时，数据预载（`load_data` 打开 6 个约 102 GB 的
NetCDF）实测要 10~20 分钟，而 uvicorn 是**先跑 lifespan startup、后 bind 端口**，所以
整个预载期间 9090 根本不监听。若只按「端口没在听 = 后端挂了」判断，就会把一个已经
加载了十几分钟的后端杀掉重来，而且每轮都重复——永远起不来。所以这里用进程创建时间
把「刚启动」和「挂死」分开：预载宽限内一律不动手，超出宽限仍不监听才判为挂死。

2026-10-12 起这条要分情况：`config.json` 的 `backend.demo_dir` 生效后，startup 只等
36 MB 的子集（秒级 bind），全量退到后台预热。此时「端口迟迟不监听」不再有任何正当理由，
继续拿 25 分钟当宽限就是把一次真故障放成 25 分钟不可达。故宽限按子集是否就绪取
`BOOT_GRACE_SUBSET` 或 `BOOT_GRACE_FULL`（见 `subset_source_ready`）。

    python scripts/health_check.py            # 只探测 + 写日志，不动任何进程
    python scripts/health_check.py --json     # 同上，输出机器可读的 JSON
    python scripts/health_check.py --fix      # 探测后按判定修复，再复探一次

日志落在 `logs/health/health-YYYY-MM-DD.log`（`logs/` 整棵已 gitignore），
修复次数护栏的状态放在 `%TEMP%/uav_health_state.json`。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT_DIR / "logs" / "health"
LOG_RETENTION_DAYS = 14

PUBLIC_BASE = "https://uav.caipiischenpi.top"
LOCAL_HOST = "127.0.0.1"

HEALTH_PATH = "/api/meta/cities"
PUBLIC_TIMEOUT = 12.0
LOCAL_TIMEOUT = 8.0
TCP_TIMEOUT = 5.0
# 全量单源时，数据预载（load_data 打开 6 个约 102 GB 的 NetCDF）实测要 10~20 分钟。
# 判定「正在预载」而不是「挂死」要用两个条件同时成立，单看进程年龄不够：
# 机器上可能有个无关的、刚启动的 python 进程，会让真死的后端看起来像在预载。
#   ① 有 python 进程的年龄在这个宽限内；
#   ② 本次「端口不再监听」持续的时间也在这个宽限内（记在状态文件里）。
# 两条任一超期就按挂死处理。定时任务 12 小时一次，跨轮次必然升级，不会永远不动手。
# 宽限取哪一档由 subset_source_ready() 决定：子集当主源时启动是秒级的，25 分钟
# 只是全量单源预载的时长，套在子集模式上等于把一次真故障放成 25 分钟不可达。
BOOT_GRACE_FULL = 25 * 60
BOOT_GRACE_SUBSET = 3 * 60
# 重启后等待端口绑定的时长。注意 uvicorn 是「先跑 lifespan startup、后 bind 端口」
# （server.py 的 lifespan.startup() 在 create_server() 之前），所以端口一旦监听就等于
# 预载已完成。等不到就报 pending（预载中），不报失败。
BACKEND_BOOT_WAIT = 300.0
# 比这更年轻的进程不参与「是否在预载」的判断：那是巡检脚本自己的启动器一类。
MIN_PROCESS_AGE_SECONDS = 5.0
PAGE_MARKER = 'id="app"'

# 护栏：同一个目标在这段时间内不重复重启，避免「重启→没起来→再重启」打成死循环。
FIX_COOLDOWN_SECONDS = 30 * 60
FIX_DAILY_CAP = 3

VERDICT_OK = "OK"
VERDICT_BACKEND_DOWN = "BACKEND_DOWN"
VERDICT_BACKEND_BOOTING = "BACKEND_BOOTING"
VERDICT_BACKEND_STALE = "BACKEND_STALE"
VERDICT_BACKEND_BUSY = "BACKEND_BUSY"
VERDICT_STATIC_BROKEN = "STATIC_BROKEN"
VERDICT_UPSTREAM_PAGE = "UPSTREAM_PAGE"
VERDICT_LOCAL_NET_ISOLATED = "LOCAL_NET_ISOLATED"
VERDICT_FRPC_DOWN = "FRPC_DOWN"
VERDICT_TUNNEL_STALL = "TUNNEL_STALL"

VERDICT_TEXT = {
    VERDICT_OK: "三层全通",
    VERDICT_BACKEND_DOWN: "后端进程已消失",
    VERDICT_BACKEND_BOOTING: "后端刚启动，正在预载气象数据（勿重启）",
    VERDICT_BACKEND_STALE: "端口未监听且进程已存活过久（疑似挂死）",
    VERDICT_BACKEND_BUSY: "端口在监听但接口无响应（疑似正在跑长任务）",
    VERDICT_STATIC_BROKEN: "后端健康但页面/静态资源不可用",
    VERDICT_UPSTREAM_PAGE: "接口通、公网页面异常（上游/OpenResty 侧）",
    VERDICT_LOCAL_NET_ISOLATED: "本机出口网络不通（重启无用）",
    VERDICT_FRPC_DOWN: "公网不通但本机正常，frpc 未在运行",
    VERDICT_TUNNEL_STALL: "公网不通但本机正常，frpc 在跑（隧道/上游待复探）",
}

# 每个 verdict 对应的修复目标；None 表示本机无处可修或不该动手。
FIX_TARGET = {
    VERDICT_BACKEND_DOWN: "backend",
    VERDICT_BACKEND_STALE: "backend",
    VERDICT_STATIC_BROKEN: "static",
    VERDICT_FRPC_DOWN: "frpc",
    VERDICT_TUNNEL_STALL: "frpc",
}

FIX_TEXT = {
    "backend": "重启后端（uvicorn，不带 --reload）",
    "static": "重建 frontend/dist 后重启后端",
    "frpc": "重启 frpc 隧道",
}


@dataclass
class Probe:
    """一次探测的结果。ok 表示「确实可用」，而不是「进程还在」。"""

    name: str
    ok: bool
    detail: str
    elapsed_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "elapsed_s": round(self.elapsed_s, 3),
        }


@dataclass
class Report:
    """巡检结果汇总，--json 直接输出它。"""

    ts: str
    verdict: str = VERDICT_OK
    reason: str = ""
    probes: list[Probe] = field(default_factory=list)
    fix_target: str | None = None
    fix_attempted: bool = False
    fix_ok: bool | None = None
    fix_pending: bool = False
    fix_detail: str = ""
    blocked: str = ""
    log_file: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "verdict": self.verdict,
            "verdict_text": VERDICT_TEXT.get(self.verdict, self.verdict),
            "reason": self.reason,
            "need_fix": self.fix_target is not None,
            "probes": [p.as_dict() for p in self.probes],
            "fix_target": self.fix_target,
            "fix_text": FIX_TEXT.get(self.fix_target or "", ""),
            "fix_attempted": self.fix_attempted,
            "fix_ok": self.fix_ok,
            "fix_pending": self.fix_pending,
            "fix_detail": self.fix_detail,
            "blocked": self.blocked,
            "log_file": self.log_file,
        }


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 配置与解释器


def read_backend_port() -> int:
    """后端端口只有一个来源：仓库根 config.json。读不到就退回 9090。"""
    try:
        cfg = json.loads((ROOT_DIR / "config.json").read_text(encoding="utf-8"))
        return int(cfg["backend"]["port"])
    except Exception:
        return 9090


def resolve_python() -> list[str]:
    """复刻 start.bat 的解释器选择顺序，保证重启出来的后端和手工起的是同一个。"""
    portable = ROOT_DIR / "backend" / ".python" / "portable" / "python.exe"
    if portable.exists():
        return [str(portable)]
    return [sys.executable]


def subset_source_ready() -> bool:
    """演示子集是否已当上主源 —— 与 `backend/data_loader.py` 的 `_demo_ready` 同一判据。

    为什么巡检要知道这件事：它决定「端口没在听」该怎么解释。

      - 子集齐（主源=子集）：startup 只 `await` 那 36 MB，几秒内就该 bind，
        全量在后台预热。此时端口长时间不监听没有正当理由，宽限必须短。
      - 子集不齐 / 没配：退回全量单源，startup 要开 6 个约 102 GB 的句柄，
        实测 10~20 分钟不监听是**正常**的，宽限必须长，否则每轮都杀掉正在预载的进程。

    文件名不在这儿另写一份：拿全量目录里的 `*.nc` 名单去子集目录逐个查存在，
    这样 `backend/config.py` 改文件名时这里不会悄悄失配（失配的后果是把好的判成坏的）。
    """
    try:
        cfg = json.loads((ROOT_DIR / "config.json").read_text(encoding="utf-8")).get("backend", {}) or {}
    except Exception:
        cfg = {}
    demo = os.environ.get("UAV_DEMO_DIR") or cfg.get("demo_dir") or ""
    if not demo:
        return False
    full = Path(os.environ.get("UAV_DATA_DIR") or cfg.get("data_dir") or (ROOT_DIR / "data"))
    try:
        names = sorted(p.name for p in full.glob("*.nc"))
    except OSError:
        return False
    if len(names) < 6:
        return False
    demo_path = Path(demo)
    return all((demo_path / n).exists() for n in names)


def read_frps_target() -> tuple[str, int] | None:
    """从 frp/frpc.toml 读 frps 地址，避免在本文件里重复写一遍服务器信息。"""
    toml_path = ROOT_DIR / "frp" / "frpc.toml"
    if not toml_path.exists():
        return None
    try:
        import tomllib

        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        return str(data["serverAddr"]), int(data["serverPort"])
    except Exception:
        return None


# -------------------------------------------------------------------- 探测实现


def _http_get(url: str, timeout: float) -> tuple[int | None, bytes, str]:
    """裸 GET，显式绕过系统代理。

    本机若挂着系统代理，访问自己的公网域名会被代走，得到的是代理的结论而不是
    服务本身的结论——这类假阳性比探测失败更难查。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "uav-health-check/1.0"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), ""
    except urllib.error.HTTPError as exc:
        return exc.code, b"", f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return None, b"", str(getattr(exc, "reason", exc))
    except Exception as exc:  # 超时、连接重置等
        return None, b"", f"{type(exc).__name__}: {exc}"


def probe_health_api(name: str, base: str, timeout: float) -> Probe:
    """探测 /api/meta/cities：既要 200，也要 body 是含非空 cities 的 JSON。"""
    url = f"{base}{HEALTH_PATH}"
    start = time.monotonic()
    status, body, err = _http_get(url, timeout)
    elapsed = time.monotonic() - start

    if status is None:
        return Probe(name, False, f"请求失败：{err}", elapsed)
    if status != 200:
        return Probe(name, False, f"HTTP {status}", elapsed)
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return Probe(name, False, f"响应不是合法 JSON（{len(body)} 字节）", elapsed)

    cities = payload.get("cities") if isinstance(payload, dict) else None
    if not isinstance(cities, list) or not cities:
        return Probe(name, False, "JSON 结构异常：缺少非空 cities", elapsed)
    return Probe(name, True, f"200，cities={len(cities)}", elapsed)


def probe_landing_page(name: str, base: str, timeout: float) -> Probe:
    """探测 / 是否真的是一份可用的 SPA 入口，并跟进它引用的首个 /assets 资源。

    只判断 200 不够：面板自带的 `location ~ .*\\.(js|css)?$` 正则会把
    /assets/index-<hash>.js 劫持成 404，表现为「页面白屏、接口全好」。
    """
    start = time.monotonic()
    status, body, err = _http_get(f"{base}/", timeout)
    if status is None:
        return Probe(name, False, f"请求失败：{err}", time.monotonic() - start)
    if status != 200:
        return Probe(name, False, f"HTTP {status}", time.monotonic() - start)

    html = body.decode("utf-8", errors="replace")
    if PAGE_MARKER not in html:
        return Probe(name, False, f"200 但不是 SPA 入口（缺 {PAGE_MARKER}）", time.monotonic() - start)

    match = re.search(r'src="(/assets/[^"]+\.js)"', html)
    if match:
        asset = match.group(1)
        a_status, _, a_err = _http_get(f"{base}{asset}", timeout)
        if a_status != 200:
            note = f"HTTP {a_status}" if a_status is not None else str(a_err)
            return Probe(name, False, f"入口页正常但 {asset} → {note}", time.monotonic() - start)
    return Probe(name, True, "SPA 入口与首屏资源均正常", time.monotonic() - start)


def probe_tcp(name: str, host: str, port: int) -> Probe:
    """TCP 连通性探测，用来区分「本机断网」与「隧道/上游挂了」。"""
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=TCP_TIMEOUT):
            return Probe(name, True, f"{host}:{port} 可达", time.monotonic() - start)
    except Exception as exc:
        return Probe(name, False, f"{host}:{port} 不可达：{exc}", time.monotonic() - start)


def _process_image_paths(image: str) -> list[str]:
    """用 tasklist 列出指定镜像名的进程 PID（只用于判断「在不在」，不做控制）。

    不走 PowerShell：它在部分受限会话里不回显 stdout，拿不到结果时会把
    「查不到」误判成「进程不在」。
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        ).stdout
    except Exception:
        return []
    pids: list[str] = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) >= 2 and parts[0].lower() == image.lower():
            pids.append(parts[1])
    return pids


def probe_frpc_process() -> Probe:
    pids = _process_image_paths("frpc.exe")
    if pids:
        return Probe("frpc 进程", True, f"运行中 PID={','.join(pids)}")
    return Probe("frpc 进程", False, "未发现 frpc.exe")


def listener_pid(port: int) -> str | None:
    """拿到监听指定端口的 PID，重启后端时才知道该收掉哪棵进程树。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(f":{port}"):
                return parts[4]
    return None


_KERNEL32: Any = None


def _kernel32() -> Any:
    """延迟加载 kernel32 并写好签名（句柄不声明 restype 会被截断成 32 位）。"""
    global _KERNEL32
    if _KERNEL32 is None:
        import ctypes
        from ctypes import wintypes

        lib = ctypes.WinDLL("kernel32", use_last_error=True)
        lib.OpenProcess.restype = wintypes.HANDLE
        lib.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        lib.CloseHandle.argtypes = [wintypes.HANDLE]
        lib.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        _KERNEL32 = lib
    return _KERNEL32


def _process_age_seconds(pid: int) -> float | None:
    """进程已运行的秒数。

    直接问内核要创建时间，不依赖 wmic（新版 Windows 已把它移除）也不依赖
    PowerShell（本环境下不回显 stdout）。判断「后端是刚启动在预载、还是挂了」全靠它。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        lib = _kernel32()
        handle = lib.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created, exited, kernel_t, user_t = (wintypes.FILETIME() for _ in range(4))
            if not lib.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                       ctypes.byref(kernel_t), ctypes.byref(user_t)):
                return None
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            started = ticks / 1e7 - 11644473600  # FILETIME 是 1601-01-01 起的 100 ns 计数
            return max(0.0, time.time() - started)
        finally:
            lib.CloseHandle(handle)
    except Exception:
        return None


def youngest_python_age() -> float | None:
    """最年轻的那个 python.exe 已运行的秒数；没有候选进程时返回 None。

    两个排除条件缺一不可：
      - 排掉自己（本脚本也是 python.exe，年龄恒为 0）。不排掉的话，后端真死时这里
        会看到「有个刚启动的 python」，于是判成「正在预载」而永远不重启。
      - 排掉刚出生几秒的进程：那是本脚本的启动器/父进程一类，不可能是「预载到一半
        的后端」；即使误伤，损失也只有几秒。
    """
    self_pid = str(os.getpid())
    ages: list[float] = []
    for pid in _process_image_paths("python.exe"):
        if pid == self_pid:
            continue
        age = _process_age_seconds(int(pid))
        if age is not None and age >= MIN_PROCESS_AGE_SECONDS:
            ages.append(age)
    return min(ages) if ages else None


# ------------------------------------------------------------------ 修复与护栏


def load_state() -> dict[str, Any]:
    path = Path(os.environ.get("TEMP", ".")) / "uav_health_state.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"fixes": []}


def save_state(state: dict[str, Any]) -> None:
    path = Path(os.environ.get("TEMP", ".")) / "uav_health_state.json"
    try:
        path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        print(f"[warn] 状态文件写入失败：{exc}", file=sys.stderr)


def guard_block(target: str, state: dict[str, Any]) -> str:
    """返回非空字符串表示这次修复被护栏拦下，说明原因。"""
    today = date.today().isoformat()
    now = time.time()
    recent = [f for f in state.get("fixes", []) if f.get("target") == target]

    last = max((f.get("at", 0) for f in recent), default=0)
    if last and now - last < FIX_COOLDOWN_SECONDS:
        wait = int((FIX_COOLDOWN_SECONDS - (now - last)) / 60)
        return f"{target} 在 {wait} 分钟前刚重启过，冷却中（{FIX_COOLDOWN_SECONDS // 60} 分钟内不重复）"

    today_count = sum(1 for f in recent if str(f.get("day")) == today)
    if today_count >= FIX_DAILY_CAP:
        return f"{target} 今日已重启 {today_count} 次，达到上限 {FIX_DAILY_CAP}"
    return ""


def record_fix(target: str, state: dict[str, Any]) -> None:
    state.setdefault("fixes", []).append(
        {"target": target, "at": time.time(), "day": date.today().isoformat()}
    )
    state["fixes"] = state["fixes"][-50:]
    save_state(state)


def _spawn_detached(cmd: list[str], cwd: Path, log_name: str) -> tuple[bool, str]:
    """脱离当前进程树启动一个常驻进程。

    必须 detached：巡检由定时任务/Agent 会话发起，父进程一退出，子进程若还在
    同一进程组里就可能被一起收走，表现为「重启完过几分钟又没了」。
    """
    temp = Path(os.environ.get("TEMP", "."))
    log_path = temp / log_name
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
    try:
        out = open(log_path, "ab")
    except Exception as exc:
        return False, f"无法打开日志 {log_path}：{exc}"
    try:
        try:
            subprocess.Popen(
                cmd, cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT,
                creationflags=flags | breakaway, close_fds=True,
            )
        except OSError:
            subprocess.Popen(
                cmd, cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT,
                creationflags=flags, close_fds=True,
            )
    except Exception as exc:
        return False, f"启动失败：{exc}"
    finally:
        out.close()
    return True, f"已拉起 {cmd[0]}，日志 {log_path}"


def _tail(path: Path, lines: int = 4) -> str:
    """读文件末尾几行，用于把启动失败的原因带回日志。"""
    try:
        rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return " | ".join(r.strip() for r in rows[-lines:] if r.strip())
    except Exception:
        return ""


def restart_backend(port: int) -> tuple[str, str]:
    """重启后端，返回 (status, detail)，status ∈ ok / pending / failed。"""
    pid = listener_pid(port)
    if pid:
        subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True, timeout=60)
        for _ in range(20):
            time.sleep(0.5)
            if listener_pid(port) is None:
                break

    py = resolve_python()
    cmd = py + [
        "-m", "uvicorn", "main:app",
        "--host", "0.0.0.0", "--port", str(port),
    ]
    ok, detail = _spawn_detached(cmd, ROOT_DIR / "backend", "uav_backend_boot.log")
    if not ok:
        return "failed", detail

    # 端口在监听 = 预载已完成：uvicorn 是先跑 lifespan startup（load_data 预载），
    # 之后才 create_server。所以这里不能只看「进程起来了」，也不能因为等不到就报失败。
    start = time.monotonic()
    deadline = start + BACKEND_BOOT_WAIT
    base = f"http://{LOCAL_HOST}:{port}"
    while time.monotonic() < deadline:
        time.sleep(2)
        if listener_pid(port) and probe_health_api("boot", base, LOCAL_TIMEOUT).ok:
            return "ok", f"后端已恢复可用（耗时 {time.monotonic() - start:.0f}s；{detail}）"

    boot_log = Path(os.environ.get("TEMP", ".")) / "uav_backend_boot.log"
    tail = _tail(boot_log)
    # 启动日志里已经有异常栈时就不是「预载中」而是真失败，必须区分开，否则会等到下一轮才处理。
    if tail and re.search(r"Traceback|Address already in use|\bError\b", tail):
        return "failed", f"启动日志报错：{tail}"

    return "pending", (
        f"已拉起，但 {BACKEND_BOOT_WAIT / 60:.0f} 分钟内接口未就绪：正在预载气象数据"
        f"（子集没当主源时，全量预载实测 10~20 分钟）。这不算启动失败，请稍后复看；{detail}"
    )


def restart_frpc() -> tuple[str, str]:
    frpc_exe = ROOT_DIR / "frp" / "frpc.exe"
    if not frpc_exe.exists():
        return "failed", f"未找到 {frpc_exe}，无法重启隧道"
    for pid in _process_image_paths("frpc.exe"):
        subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True, timeout=60)
        time.sleep(0.5)
    ok, detail = _spawn_detached([str(frpc_exe), "-c", "frpc.toml"], ROOT_DIR / "frp", "uav_frpc.log")
    if not ok:
        return "failed", detail
    # 隧道重连是秒级的，但公网恢复还要看 frps 注册与上游；这里等 20 秒探一次，
    # 探不到也不算失败——真正是否恢复由调用方复探公网那一层来判定。
    for _ in range(10):
        time.sleep(2)
        if probe_health_api("tunnel", PUBLIC_BASE, PUBLIC_TIMEOUT).ok:
            return "ok", f"frpc 已重连且公网已恢复（{detail}）"
    return "pending", f"frpc 已重新拉起，但 20 秒内公网仍未恢复（{detail}）"


def rebuild_dist() -> tuple[str, str]:
    """重建 dist。这一步是阻塞的，且必须在后端重启之前跑完。

    `vite build` 会先清空 dist，而 `/` 的静态挂载只在 main.py import 时判断一次；
    两个动作挨太近就会撞出「挂载失败、整站 404」。
    """
    try:
        proc = subprocess.run(
            "npm run build",
            cwd=str(ROOT_DIR / "frontend"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            shell=True,
        )
    except Exception as exc:
        return "failed", f"npm run build 失败：{exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        return "failed", f"npm run build 退出码 {proc.returncode}：{' | '.join(tail)}"
    if not (ROOT_DIR / "frontend" / "dist" / "index.html").exists():
        return "failed", "构建成功但 dist/index.html 缺失"
    return "ok", "frontend/dist 已重建"


# -------------------------------------------------------------------- 判定逻辑


def diagnose_local_outage(port: int, probes: list[Probe],
                          state: dict[str, Any] | None = None) -> tuple[str, str, list[Probe]]:
    """本机接口不通时，先分清「真死了」「还在预载」「只是在忙」。

    这是整套巡检里最要紧的一步。**全量单源**时数据预载（6 个约 102 GB 的 NetCDF）要
    10~20 分钟，而 uvicorn 是**先跑 lifespan startup、后 bind 端口**，所以预载期间
    9090 根本不监听。只看端口就会把「正在预载」判成「后端挂了」，然后杀掉一个已经
    加载了十几分钟的后端，让它从头再来一遍——而且每轮都重复，永远起不来。
    `config.json` 配了 `demo_dir` 且六个文件齐全时预载不再是启动的一部分（子集秒级
    bind、全量后台预热），宽限随之收紧到 `BOOT_GRACE_SUBSET`。

    但光看「有没有年轻的 python 进程」也不可靠：机器上可能有个无关的、刚启动的
    python，会让真死的后端看起来像在预载。所以再要求「中断持续时间」也还在宽限内，
    两个条件同时成立才算预载中。
    """
    st = state if state is not None else {}

    pid = listener_pid(port)
    if pid:
        st.pop("outage_since", None)
        probes.append(Probe("后端进程", True, f"端口 {port} 在监听（PID {pid}）"))
        return (
            VERDICT_BACKEND_BUSY,
            f"端口在监听、但接口 {LOCAL_TIMEOUT:.0f}s 内无响应：单 worker 串行时长寻路会排队，"
            f"也可能是进程卡住。不重启——杀了它会把在线用户的请求一起打断",
            probes,
        )

    age = youngest_python_age()
    if age is None:
        st.pop("outage_since", None)
        probes.append(Probe("后端进程", False, "没有 python.exe 在运行"))
        return VERDICT_BACKEND_DOWN, "端口未监听，且系统里没有任何 python 进程", probes

    since = st.setdefault("outage_since", time.time())
    outage = time.time() - since
    probes.append(Probe(
        "后端进程", True,
        f"有 python 进程（最年轻的已运行 {age / 60:.1f} 分钟），端口未监听已持续 {outage / 60:.1f} 分钟",
    ))

    grace = BOOT_GRACE_SUBSET if subset_source_ready() else BOOT_GRACE_FULL
    mode = "子集当主源，启动只需秒级" if grace == BOOT_GRACE_SUBSET else "全量单源，预载实测 10~20 分钟"
    if age < grace and outage < grace:
        return (
            VERDICT_BACKEND_BOOTING,
            f"端口未监听，但进程只启动了 {age / 60:.1f} 分钟、中断也只持续了 "
            f"{outage / 60:.1f} 分钟，仍在宽限 {grace / 60:.0f} 分钟内（{mode}）。"
            f"重启会打断启动，故不动手；请稍后复看",
            probes,
        )
    return (
        VERDICT_BACKEND_STALE,
        f"端口未监听：进程已运行 {age / 60:.1f} 分钟、中断已持续 {outage / 60:.1f} 分钟，"
        f"至少一项超出宽限 {grace / 60:.0f} 分钟（{mode}），按挂死处理",
        probes,
    )


def assess(port: int, state: dict[str, Any] | None = None) -> tuple[str, str, list[Probe]]:
    """三层探活并给出判定。顺序是先本机、后隧道：本机是可控部分。

    先判本机还有一个副作用是对的：后端没起来时，公网必然是 502，但那是结果不是原因，
    不能据此去重启 frpc。
    """
    st = state if state is not None else {}
    probes: list[Probe] = []
    local_base = f"http://{LOCAL_HOST}:{port}"

    local_api = probe_health_api("本机接口", local_base, LOCAL_TIMEOUT)
    probes.append(local_api)
    if not local_api.ok:
        return diagnose_local_outage(port, probes, st)
    st.pop("outage_since", None)

    local_page = probe_landing_page("本机页面", local_base, LOCAL_TIMEOUT)
    probes.append(local_page)
    if not local_page.ok:
        return VERDICT_STATIC_BROKEN, f"{local_page.detail}", probes

    public_api = probe_health_api("公网接口", PUBLIC_BASE, PUBLIC_TIMEOUT)
    probes.append(public_api)
    if public_api.ok:
        public_page = probe_landing_page("公网页面", PUBLIC_BASE, PUBLIC_TIMEOUT)
        probes.append(public_page)
        if public_page.ok:
            return VERDICT_OK, "公网与本机两层均正常", probes
        return VERDICT_UPSTREAM_PAGE, f"{public_page.detail}", probes

    # 公网不通、本机正常 —— 先确认是本机出口网络的问题，还是隧道/上游的问题。
    target = read_frps_target()
    if target:
        frps = probe_tcp("frps 隧道服务", target[0], target[1])
    else:
        frps = Probe("frps 隧道服务", False, "读不到 frp/frpc.toml，跳过")
    probes.append(frps)
    if not frps.ok:
        return VERDICT_LOCAL_NET_ISOLATED, f"{frps.detail}（重启本机服务无用）", probes

    frpc = probe_frpc_process()
    probes.append(frpc)
    if not frpc.ok:
        return VERDICT_FRPC_DOWN, "frpc 未运行，隧道断了", probes

    return VERDICT_TUNNEL_STALL, f"公网不通但本机与 frps 均可达：{public_api.detail}", probes


def run_fix(target: str, port: int, report: Report) -> None:
    report.fix_attempted = True
    if target == "backend":
        status, detail = restart_backend(port)
    elif target == "frpc":
        status, detail = restart_frpc()
    elif target == "static":
        status, detail = rebuild_dist()
        if status == "ok":
            status, detail2 = restart_backend(port)
            detail = f"{detail}；{detail2}"
    else:
        status, detail = "failed", f"未知修复目标 {target}"
    report.fix_ok = status == "ok"
    report.fix_pending = status == "pending"
    report.fix_detail = detail


# -------------------------------------------------------------------- 日志输出


def write_log(report: Report) -> str:
    """追加一行人类可读的巡检记录，并顺手清理过期日志。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"health-{date.today().isoformat()}.log"

    failed = [p for p in report.probes if not p.ok]
    probe_txt = ",".join(f"{p.name}={'ok' if p.ok else 'FAIL'}" for p in report.probes)
    line = f"{report.ts}  {report.verdict}  {probe_txt}"
    if failed:
        line += "  | " + "; ".join(f"{p.name}: {p.detail}" for p in failed)
    if report.blocked:
        line += f"  | 修复被拦：{report.blocked}"
    if report.fix_attempted:
        tag = "成功" if report.fix_ok else ("预载中" if report.fix_pending else "失败")
        line += f"  | 修复[{report.fix_target}] {tag}：{report.fix_detail}"
    line += "\n"

    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)

    cutoff = time.time() - LOG_RETENTION_DAYS * 86400
    for old in LOG_DIR.glob("health-*.log"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except Exception:
            pass
    return str(path)


def print_human(report: Report) -> None:
    print(f"[{report.ts}] {report.verdict} — {VERDICT_TEXT.get(report.verdict, '')}")
    for p in report.probes:
        print(f"  {'ok  ' if p.ok else 'FAIL'} {p.name}: {p.detail}")
    if report.reason:
        print(f"  判定依据: {report.reason}")
    if report.blocked:
        print(f"  修复被拦: {report.blocked}")
    if report.fix_attempted:
        tag = "成功" if report.fix_ok else ("预载中" if report.fix_pending else "失败")
        print(f"  修复[{report.fix_target}] {tag}: {report.fix_detail}")
    if report.fix_target and not report.fix_attempted:
        print(f"  建议动作: {FIX_TEXT.get(report.fix_target, report.fix_target)}")
    print(f"  日志: {report.log_file}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="公网服务可用性巡检与自动修复")
    parser.add_argument("--fix", action="store_true", help="按判定执行修复并复探")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("--no-log", action="store_true", help="本次不写日志文件")
    parser.add_argument("--port", type=int, default=0,
                        help="覆盖本机端口（诊断/演练用；默认读 config.json）")
    args = parser.parse_args()

    port = args.port or read_backend_port()
    state = load_state()
    report = Report(ts=now_str())
    report.verdict, report.reason, report.probes = assess(port, state)
    report.fix_target = FIX_TARGET.get(report.verdict)

    if report.fix_target and args.fix:
        blocked = guard_block(report.fix_target, state)
        if blocked:
            report.blocked = blocked
        else:
            record_fix(report.fix_target, state)
            run_fix(report.fix_target, port, report)
            # 我们刚拉起一个新后端：全量单源时它要预载 10~20 分钟，把「中断持续」计时
            # 重置到此刻，否则复探会把这次正常的预载判成挂死。
            if report.fix_target == "backend" and (report.fix_ok or report.fix_pending):
                state["outage_since"] = time.time()
            # 复探一次：修复动作不算完成，复探通过才算。
            after, reason_after, probes_after = assess(port, state)
            report.probes += probes_after
            if after == VERDICT_OK:
                report.reason = f"{report.reason} → 修复后复探通过"
            elif report.fix_pending and after == VERDICT_BACKEND_BOOTING:
                # 后端重启后先预载才可用（子集当主源几秒，全量单源 10~20 分钟），
                # 复探看到「预载中」是预期结果，不能当成修复失败，否则会被冷却护栏锁住半小时。
                report.verdict = after
                report.reason = (
                    f"{report.reason} → 已重新拉起，复探显示仍在启动/预载气象数据"
                    f"（子集当主源几秒即可用，全量单源 10~20 分钟），本轮不判为失败"
                )
            else:
                report.verdict = after
                report.reason = f"{report.reason} → 修复后仍为 {after}：{reason_after}"

    save_state(state)

    if not args.no_log:
        report.log_file = write_log(report)

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=1))
    else:
        print_human(report)

    # 退出码：0 正常，1 需要人工介入（含修复后仍不健康）。定时任务可据此判断。
    return 0 if report.verdict == VERDICT_OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
