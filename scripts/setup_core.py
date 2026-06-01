"""
UAV Planner 环境配置核心脚本
- Windows: 优先用系统 Python, 没有则自动下载 embeddable Python
- Linux/Mac: 必须用系统 Python (会给出安装提示)
"""
import os
import sys

# 强制 UTF-8 输出 (Windows cmd 默认 GBK, 会让中文乱码)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import shutil
import urllib.request
import zipfile
import subprocess
from pathlib import Path

# ---- 配置 ----
PYTHON_MIN = (3, 10)
EMBED_PYTHON_VERSION = "3.10.11"
EMBED_PYTHON_URL = (
    f"https://www.python.org/ftp/python/{EMBED_PYTHON_VERSION}/"
    f"python-{EMBED_PYTHON_VERSION}-embed-amd64.zip"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# ---- 路径 ----
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
BACKEND = ROOT / "backend"
PYTHON_DIR = BACKEND / ".python"
PORTABLE_DIR = PYTHON_DIR / "portable"
PORTABLE_PY = PORTABLE_DIR / ("python.exe" if os.name == "nt" else "bin/python3")
REQUIREMENTS = BACKEND / "requirements.txt"

IS_WINDOWS = os.name == "nt"

ANSI = {
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "cyan": "\033[36m", "dim": "\033[2m", "reset": "\033[0m",
}


def cprint(tag: str, msg: str, color: str = "") -> None:
    """彩色输出。Windows 10+ 的 cmd.exe 默认支持 ANSI。"""
    c = ANSI.get(color, "")
    r = ANSI["reset"]
    print(f"{c}[{tag}]{r} {msg}")


def get_py_version(exe: str):
    """运行 python -c 解析版本。返回 (major, minor) 或 None。"""
    try:
        r = subprocess.run(
            [exe, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            a, b = r.stdout.strip().split(".")
            return int(a), int(b)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def find_system_python():
    """在 PATH 里找满足版本要求的 Python。返回可执行文件名 (例如 'python') 或 None。"""
    candidates = ["py", "python", "python3"] if IS_WINDOWS else ["python3", "python"]
    for name in candidates:
        ver = get_py_version(name)
        if ver and ver >= PYTHON_MIN:
            return name
    return None


def download_with_progress(url: str, target: Path, desc: str) -> None:
    """带进度条的下载。"""
    cprint("下载", desc)
    target.parent.mkdir(parents=True, exist_ok=True)

    def reporthook(block_num, block_size, total_size):
        downloaded = block_num * block_size
        pct = min(100, downloaded * 100 // total_size) if total_size > 0 else 0
        bar = "=" * (30 * pct // 100) + " " * (30 - 30 * pct // 100)
        mb = downloaded / 1024 / 1024
        print(f"\r  [{bar}] {pct:3d}%  {mb:5.1f} MB", end="", flush=True)

    try:
        urllib.request.urlretrieve(url, target, reporthook)
        print()
    except Exception as e:
        print()
        cprint("错误", f"下载失败: {e}\n  URL: {url}", "red")
        sys.exit(1)


def enable_site_packages(embed_dir: Path) -> None:
    """embeddable Python 默认禁用 site-packages, 需改 ._pth 文件。"""
    pth_files = list(embed_dir.glob("python*._pth"))
    if not pth_files:
        cprint("警告", "未找到 ._pth 文件, site-packages 可能未启用", "yellow")
        return
    for pth in pth_files:
        text = pth.read_text(encoding="utf-8", errors="ignore")
        if "#import site" in text:
            pth.write_text(text.replace("#import site", "import site"), encoding="utf-8")
            cprint("配置", f"启用 site-packages ({pth.name})")
        else:
            cprint("配置", f"site-packages 已启用 ({pth.name})")


def install_embedded_python() -> Path:
    """下载并解压 embeddable Python 到 backend/.python/portable/。"""
    if PORTABLE_PY.exists():
        cprint("跳过", f"便携 Python 已存在: {PORTABLE_PY.relative_to(ROOT)}")
        return PORTABLE_PY

    if not IS_WINDOWS:
        cprint("错误", "非 Windows 平台不支持自动下载 Python, 请先安装系统 Python", "red")
        sys.exit(1)

    PORTABLE_DIR.mkdir(parents=True, exist_ok=True)
    embed_zip = PORTABLE_DIR / "python-embed.zip"
    get_pip_path = PORTABLE_DIR / "get-pip.py"

    # 1. 下载并解压 embeddable
    download_with_progress(
        EMBED_PYTHON_URL, embed_zip,
        f"Python {EMBED_PYTHON_VERSION} embeddable (~10 MB)",
    )
    cprint("解压", f"-> {PORTABLE_DIR.relative_to(ROOT)}")
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(PORTABLE_DIR)
    embed_zip.unlink()

    # 2. 启用 site-packages
    enable_site_packages(PORTABLE_DIR)

    # 3. 安装 pip
    download_with_progress(GET_PIP_URL, get_pip_path, "get-pip.py (~2 MB)")
    cprint("安装", "pip 到便携 Python")
    subprocess.check_call([str(PORTABLE_PY), str(get_pip_path)])
    get_pip_path.unlink()

    return PORTABLE_PY


def ensure_pip(python_exe: str) -> None:
    """确保 pip 可用, 没有则尝试 ensurepip。"""
    r = subprocess.run(
        [python_exe, "-m", "pip", "--version"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        return
    cprint("安装", "pip (via ensurepip)")
    try:
        subprocess.check_call([python_exe, "-m", "ensurepip", "--upgrade"])
    except subprocess.CalledProcessError:
        cprint("错误", "pip 不可用且 ensurepip 失败", "red")
        sys.exit(1)


def install_requirements(python_exe: str, *, offline: bool = False) -> None:
    """pip install -r requirements.txt。offline=True 时使用 --no-index。"""
    if not REQUIREMENTS.exists():
        cprint("错误", f"未找到依赖文件: {REQUIREMENTS}", "red")
        sys.exit(1)

    ensure_pip(python_exe)
    rel = REQUIREMENTS.relative_to(ROOT)
    cprint("安装", f"依赖 (来自 {rel}){'  [离线模式]' if offline else ''}")

    cmd = [python_exe, "-m", "pip", "install", "-r", str(REQUIREMENTS),
           "--disable-pip-version-check"]
    if offline:
        cmd.append("--no-index")

    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError:
        cprint("错误", "依赖安装失败, 可重试或检查网络", "red")
        sys.exit(1)


def main():
    cprint("UAV Planner", "环境配置", "cyan")
    print(f"  工作目录: {ROOT}")
    print(f"  Python 要求: >= {'.'.join(map(str, PYTHON_MIN))}")
    print()

    # 1. 解析 Python 解释器
    cprint("1/3", "查找 Python 解释器")
    if PORTABLE_PY.exists():
        python_exe = str(PORTABLE_PY)
        cprint("使用", f"便携 Python ({python_exe})")
    else:
        py = find_system_python()
        if py:
            python_exe = py
            cprint("使用", f"系统 Python: {py}")
        else:
            cprint("未找到", f"系统 Python {'.'.join(map(str, PYTHON_MIN))}+", "yellow")
            python_exe = str(install_embedded_python())

    # 2. 验证版本
    ver = get_py_version(python_exe)
    if not ver or ver < PYTHON_MIN:
        cprint("错误", f"Python 版本过低: {ver}, 需要 >= {'.'.join(map(str, PYTHON_MIN))}", "red")
        sys.exit(1)
    cprint("版本", f"Python {'.'.join(map(str, ver))} 满足要求", "green")
    print()

    # 3. 装依赖
    cprint("2/3", "安装 Python 依赖")
    install_requirements(python_exe)
    print()

    # 4. 收尾
    cprint("3/3", "完成")
    print()
    cprint("完成", "环境已就绪", "green")
    print(f"  下一步: 运行 .\\start.bat (Windows) 或 ./start.sh (Linux/Mac) 启动服务")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        cprint("中断", "已取消", "yellow")
        sys.exit(130)
