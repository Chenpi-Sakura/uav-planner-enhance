"""给 Agent「看」图：把某一区域的图层渲染成小图，或直接用用户上传的附件。

存在的理由：对话里全是数字，而**空间形态**只有看图才知道。数字能说「最大 TKE
5.14」，说不出「高值区在山脊东侧、航线擦着边缘过、那片是连成一片还是零散几块」。
这一层把图真正送到模型眼前。

图的来源有两条：
  * **地图区域** —— 复用 ``data_loader.get_overlay_image``（前端贴图用的就是它），
    所以 AI 看到的就是用户此刻在地图上看到的那张，不是另画一套示意图。
  * **用户附件** —— 前端上传的图片原样透传，不做任何加工。

尺寸与格式按多模态的代价来定（长边 <= ``MAX_EDGE``、JPEG q=``JPEG_QUALITY``）：
图越大按 token 计越贵，而这层图只需要看清形态分布，不需要看清文字。
"""

import base64
import io
import math
from typing import Any, Dict, List, Optional

from data_loader import data_loader
from timebase import to_data_time_str

# 多模态按面积计 token，768 的长边足够看清空间形态，再大只是多花钱。
MAX_EDGE = 768
JPEG_QUALITY = 60
# 单次渲染出的 JPEG 上限（MB）。超了就不是「看不清」而是「根本读不动」了。
MAX_BYTES = 900 * 1024

# 前端能点的图层键 -> get_overlay_image 认识的图层名
_LAYER_ALIAS = {"tke": "TKE", "edr": "EDR", "wind": "wind", "temp": "tc",
                "rh": "rh", "q": "q"}


def _shrink(png_bytes: bytes) -> str:
    """PNG -> 降采样 -> JPEG -> data URL。返回空串表示这一步失败。"""
    try:
        from PIL import Image
    except Exception:
        return ""
    try:
        img = Image.open(io.BytesIO(png_bytes))
        # 图层是带透明底的，直接转 RGB 会把透明区变成黑的；先衬一层浅底
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.split()[-1])
            img = flat
        else:
            img = img.convert("RGB")
        w, h = img.size
        scale = min(1.0, MAX_EDGE / max(w, h))
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                             Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        data = buf.getvalue()
        if len(data) > MAX_BYTES:
            return ""
        return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return ""


def _is_blank(img) -> bool:
    """判断图里到底有没有内容。

    覆盖范围外的框会渲染出一张几乎纯色的图，**照样返回 200**。那种图给模型看，
    它会对着空白「描述」出并不存在的地形与高值区 —— 和之前那个「tke=0 判成低风险」
    是同一类错误：把「没数据」说成「有数据且很安全」。所以宁可在这里失败。
    """
    from PIL import Image
    small = img.convert("RGB").resize((48, 48))
    colors = small.getcolors(maxcolors=1 << 16)
    if not colors:                      # 颜色数超过上限，视为内容丰富
        return False
    total = sum(c for c, _ in colors)
    top = max((c for c, _ in colors), default=0)
    return top / total > 0.985         # 单一颜色占了 98.5% 以上 = 实质空白


def _render_field(lat: float, lon: float, km: float, layer: str,
                  date: str, time: str):
    """给模型渲染一张**按本区域自身数值范围**归一的图。

    为什么不复用 ``get_overlay_image``：那张图的色标是写死的绝对值（TKE 0~30），
    为了让地图上「深蓝 = 安全」这个语义稳定。而这条航线的真实 TKE 只有 0.9~5.1，
    全挤在色标最低 17%，出来的图是一片几乎无差别的深蓝 —— 模型看不出任何形态，
    等于给了它一张噪声。地图要绝对口径，模型要相对口径，所以这里单独渲染。

    附色标刻度是为了让模型能把颜色读成数值，而不是凭感觉说「那边比较红」。
    """
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from meteorology import pulsation_tke, edr_from_tke

    bbox = bbox_around(lat, lon, km)
    half = int(getattr(settings := __import__("config").settings,
                       "TURB_WINDOW_HALF_HOURS", 6))
    t_data = np.datetime64(to_data_time_str(f"{date}T{time or '08:00'}"))
    bbox_t = (bbox["min_lat"], bbox["max_lat"], bbox["min_lon"], bbox["max_lon"])

    def slab(var):
        r = data_loader.read_slab(var, t_data, half * 2 + 1, bbox_t)
        if r is None or r[0] is None:
            return None, None, None
        vals, lats, lons, _ = r
        return vals, lats, lons

    if layer in ("wind", "temp", "rh", "q"):
        var = {"wind": "u", "temp": "tc", "rh": "rh", "q": "qvapor"}[layer]
        vals, lats, lons = slab(var)
        if vals is None:
            return None, "读不到该区域数据"
        field = np.asarray(vals)[half]          # 取目标时刻那一帧
        label = {"wind": "风速分量 u (m/s)", "temp": "温度 (℃)",
                 "rh": "相对湿度 (%)", "q": "水汽 (g/kg)"}[layer]
        cmap = "viridis"
    else:
        u, lats, lons = slab("u")
        v, _, _ = slab("v")
        w, _, _ = slab("w")
        if u is None or v is None or w is None:
            return None, "读不到该区域的风场数据"
        tke = pulsation_tke(np.asarray(u), np.asarray(v), np.asarray(w))
        if layer == "edr":
            field = edr_from_tke(tke)[half]
            label = "EDR (m^(2/3)/s)"
            cmap = "magma"
        else:
            field = tke[half]
            label = "TKE (m²/s²)"
            cmap = "inferno"

    field = np.asarray(field, dtype=float)
    if not np.isfinite(field).any():
        return None, "该区域这一时刻没有有效数据"

    # 按本区域自己的分位数归一：区域里最低~最高那 2%~98% 铺满色标，
    # 极端值压在两端。写死绝对阈值就是上面那张「一片深蓝」。
    lo, hi = np.nanpercentile(field, 2), np.nanpercentile(field, 98)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < 1e-9:
        return None, "该区域数值几乎没有差异，画不出形态"

    fig, ax = plt.subplots(figsize=(6.4, 6.0), dpi=100)
    im = ax.imshow(field, cmap=cmap, vmin=lo, vmax=hi,
                   extent=[bbox["min_lon"], bbox["max_lon"],
                           bbox["min_lat"], bbox["max_lat"]],
                   origin="lower", aspect="auto")
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
    cb.set_label(label, fontsize=9)
    cb.ax.tick_params(labelsize=8)
    # 标注一律用 ASCII：容器里没配中文字体，写中文会渲染成一排豆腐块，
    # 而模型要读的是数值和形态，不是中文散文。
    ax.set_title(f"{layer.upper()}  {date} {time}  center {lat:.2f}N {lon:.2f}E  "
                 f"radius {_r(km)} km", fontsize=9)
    ax.tick_params(labelsize=7)
    ax.set_xlabel("lon", fontsize=8)
    ax.set_ylabel("lat", fontsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="PNG", dpi=100)
    plt.close(fig)
    return buf.getvalue(), None


def _r(km: float) -> int:
    return int(round(max(1.0, min(500.0, float(km or 30)))))


def bbox_around(lat: float, lon: float, km: float) -> Dict[str, float]:
    """以某点为中心、给定半径的经纬度包围盒。

    经度差按 cos(lat) 收窄：同样一度经度在高纬度代表的地面距离更短，
    不收窄的话在 30°N 画出来的框东西向会比南北向宽一大截。
    """
    km = max(1.0, min(500.0, float(km or 30)))
    dlat = km / 110.574
    dlon = km / (111.320 * max(0.2, math.cos(math.radians(lat))))
    return {
        "min_lat": max(-90.0, lat - dlat),
        "max_lat": min(90.0, lat + dlat),
        "min_lon": max(-180.0, lon - dlon),
        "max_lon": min(180.0, lon + dlon),
    }


def render_region_image(lat: float, lon: float, km: float, layer: str,
                        date: str, time: str) -> Dict[str, Any]:
    """渲染一块区域的图层，返回 data URL。渲染不出来就如实说，不要给一张空白图。"""
    if not getattr(data_loader, "_loaded", False):
        return {"ok": False, "reason": "气象数据尚未加载完成，看不了图。"}

    if layer not in _LAYER_ALIAS:
        layer = "tke"      # 端点公网无鉴权，图层名不能让前端随便传

    try:
        png, err = _render_field(lat, lon, km, layer, date, time)
    except Exception as exc:
        return {"ok": False, "reason": f"渲染失败：{type(exc).__name__}"}
    if not png:
        return {"ok": False, "reason": err or "该区域没有可渲染的气象数据。"}

    url = _shrink(png)
    if not url:
        return {"ok": False, "reason": "渲染出的图过大或转换失败，看不了。"}

    try:
        from PIL import Image
        if _is_blank(Image.open(io.BytesIO(png))):
            return {"ok": False, "reason": "该区域渲染出来是空图（可能超出数据覆盖范围）。"}
    except Exception:
        pass

    return {
        "ok": True,
        "image": url,
        "layer": layer,
        "date": date,
        "time": time,
        "center": {"lat": round(lat, 4), "lon": round(lon, 4)},
        "radius_km": _r(km),
    }
