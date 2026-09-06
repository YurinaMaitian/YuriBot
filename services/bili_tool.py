"""
B站分享解析：链接解析(BV+p) / 卡片标题反查(搜狗) / 视频信息 / 官方AI总结 / 封面哈希消歧
依赖: aiohttp, PIL(已有), config.BILI_COOKIE
"""

import asyncio
import hashlib
import json
import re
import time
import urllib.parse

import aiohttp
from PIL import Image

from config import BILI_COOKIE

_BV_RE = re.compile(r"BV1[a-zA-Z0-9]{9}")
_AV_RE = re.compile(r"/video/av(\d+)")
_B23_RE = re.compile(r"https?://b23\.tv/[A-Za-z0-9]+")
_BILI_URL_RE = re.compile(r"https?://(?:www\.)?bilibili\.com/video/(BV1[a-zA-Z0-9]{9})")
_P_RE = re.compile(r"[?&]p=(\d+)")
_TITLE_RE = re.compile(r"title:\s*(.+)")
_PREVIEW_RE = re.compile(r"preview:\s*(https?://\S+)")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_MIXIN_TAB = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]

_TIMEOUT = aiohttp.ClientTimeout(total=15)
_session: aiohttp.ClientSession | None = None
_wbi_keys: tuple[str, str] | None = None
_wbi_keys_at: float = 0.0

# ---- 简单 TTL 缓存（内存） ----
_cache: dict[str, tuple[float, object]] = {}
_VIEW_TTL = 7 * 24 * 3600  # 视频信息：不变，长缓存
_SUMMARY_TTL = 24 * 3600  # AI总结：不变，但留余量
_NEG_TTL = 6 * 3600  # 无AI总结的负缓存


def _cache_get(key: str, ttl: float):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def _cache_set(key: str, value):
    _cache[key] = (time.time(), value)


def _headers(with_cookie: bool = False) -> dict:
    h = {"User-Agent": _UA, "Referer": "https://www.bilibili.com"}
    if with_cookie and BILI_COOKIE:
        h["Cookie"] = BILI_COOKIE
    return h


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=_TIMEOUT)
        # 预热拿 b_nut
        try:
            await _session.get(
                "https://www.bilibili.com", headers=_headers(with_cookie=True)
            )
        except Exception as e:
            print(f"[bili] 预热失败: {e}")
    return _session


def wbi_sign(params: dict, img_key: str, sub_key: str) -> dict:
    mixin = "".join((img_key + sub_key)[i] for i in _MIXIN_TAB)[:32]
    params = {
        k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()
    }
    params["wts"] = int(time.time())
    query = urllib.parse.urlencode(sorted(params.items()))
    params["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return params


async def _get_wbi_keys(force: bool = False) -> tuple[str, str] | None:
    global _wbi_keys, _wbi_keys_at
    if not force and _wbi_keys and time.time() - _wbi_keys_at < 3600:
        return _wbi_keys
    s = await _get_session()
    try:
        async with s.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=_headers(with_cookie=True),
        ) as r:
            data = await r.json(content_type=None)
        wbi = (data.get("data") or {}).get("wbi_img") or {}
        img_key = wbi["img_url"].rsplit("/", 1)[-1].split(".")[0]
        sub_key = wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0]
        _wbi_keys = (img_key, sub_key)
        _wbi_keys_at = time.time()
        return _wbi_keys
    except Exception as e:
        print(f"[bili] wbi keys 获取失败: {type(e).__name__}: {e}")
        return None


# ========== 对外 API ==========


def detect_bili_card(content: str) -> tuple[str, str]:
    """识别B站小程序卡片。返回 (标题, 封面预览URL)，非卡片返回 ('', '')"""
    if "[卡片消息]" not in content or "哔哩哔哩" not in content:
        return "", ""
    m = _TITLE_RE.search(content)
    p = _PREVIEW_RE.search(content)
    title = m.group(1).strip() if m else ""
    preview = p.group(1).strip() if p else ""
    return title, preview


async def resolve_bv(text: str) -> tuple[str, int]:
    """
    从文本里解析 BV 号 + 分P。返回 (vid, p)；vid 可能是 'BV...' 或 'av...'。
    支持：直链、bv号明文、b23短链。解析不到返回 ('', 0)
    """
    p_m = _P_RE.search(text)
    p = int(p_m.group(1)) if p_m else 1

    m = _BILI_URL_RE.search(text) or _BV_RE.search(text)
    if m:
        return (m.group(1) if m.lastindex else m.group(0)), p

    m = _B23_RE.search(text)
    if m:
        s = await _get_session()
        try:
            async with s.get(
                m.group(0), headers=_headers(), allow_redirects=False
            ) as r:
                loc = r.headers.get("Location", "")
            bv = _BV_RE.search(loc)
            if bv:
                return bv.group(0), p
        except Exception as e:
            print(f"[bili] b23解析失败: {type(e).__name__}: {e}")
    return "", 0


async def search_video_by_title(title: str, limit: int = 5) -> list[str]:
    kw = re.sub(r"【[^】]*】", "", title).strip() or title
    for engine in (_sogou_search, _baidu_search):
        ids = await engine(kw, limit)
        if ids:
            return ids
        print(f"[bili] {engine.__name__} 无结果，尝试下一引擎")
    return []


async def _sogou_search(kw: str, limit: int) -> list[str]:
    q = urllib.parse.quote(f'site:bilibili.com/video "{kw}"')
    s = await _get_session()
    try:
        async with s.get(
            f"https://www.sogou.com/web?query={q}", headers=_headers()
        ) as r:
            html = await r.text()
            status = r.status
    except Exception as e:
        print(f"[bili] 搜狗请求失败: {type(e).__name__}: {e}")
        return []
    print(
        f"[bili] 搜狗 status={status}, 长度={len(html)}, "
        f"含bilibili={'bilibili' in html}, 疑似风控={'验证码' in html or 'antispider' in html.lower()}"
    )
    ids: list[str] = []
    for bv in _BV_RE.findall(html):
        if bv not in ids:
            ids.append(bv)
    for av in _AV_RE.findall(html):
        if f"av{av}" not in ids:
            ids.append(f"av{av}")
    return ids[:limit]


_BAIDU_LINK_RE = re.compile(r'href="(https://www\.baidu\.com/link\?url=[^"]+)"')


async def _baidu_search(kw: str, limit: int) -> list[str]:
    q = urllib.parse.quote(f"site:bilibili.com/video {kw}")
    s = await _get_session()
    try:
        async with s.get(f"https://www.baidu.com/s?wd={q}", headers=_headers()) as r:
            html = await r.text()
            status = r.status
    except Exception as e:
        print(f"[bili] 百度请求失败: {type(e).__name__}: {e}")
        return []
    print(f"[bili] 百度 status={status}, 长度={len(html)}")
    links = list(dict.fromkeys(_BAIDU_LINK_RE.findall(html)))[:5]
    ids: list[str] = []

    async def _resolve(u: str):
        try:
            async with s.get(u, headers=_headers(), allow_redirects=True) as r2:
                final = str(r2.url)
            m = _BV_RE.search(final)
            av = _AV_RE.search(final)
            if m and m.group(0) not in ids:
                ids.append(m.group(0))
            elif av and f"av{av.group(1)}" not in ids:
                ids.append(f"av{av.group(1)}")
        except Exception as e:
            print(f"[bili] 百度跳转解析失败: {type(e).__name__}")

    await asyncio.gather(*[_resolve(u) for u in links])
    print(f"[bili] 百度解包出 {len(ids)} 个候选")
    return ids[:limit]


_NEG = "__NEGATIVE__"


async def get_video_info(vid: str) -> dict | None:
    """view 接口：标题/UP/封面/分P。缓存7天。vid 支持 BV 或 av。"""
    cached = _cache_get(f"view:{vid}", _VIEW_TTL)
    if cached is not None:
        return None if cached == _NEG else cached

    params = (
        {"bvid": vid}
        if vid.startswith("BV")
        else {"aid": vid[2:] if vid.startswith("av") else vid}
    )
    s = await _get_session()
    try:
        async with s.get(
            "https://api.bilibili.com/x/web-interface/view",
            params=params,
            headers=_headers(),
        ) as r:
            data = await r.json(content_type=None)
    except Exception as e:
        print(f"[bili] view失败: {type(e).__name__}: {e}")
        return None
    if data.get("code") != 0:
        print(f"[bili] view错误: code={data.get('code')} {data.get('message')}")
        _cache_set(f"view:{vid}", None)
        return None

    d = data["data"]
    info = {
        "bvid": d["bvid"],
        "title": d["title"],
        "cover": "https:" + d["pic"] if d["pic"].startswith("//") else d["pic"],
        "up_name": d["owner"]["name"],
        "up_mid": d["owner"]["mid"],
        "pages": [
            {
                "page": p_["page"],
                "cid": p_["cid"],
                "part": p_.get("part", ""),
                "first_frame": p_.get("first_frame", ""),
            }
            for p_ in d.get("pages", [])
        ],
    }
    _cache_set(f"view:{vid}", info)
    return info


async def get_ai_summary(bvid: str, cid: int, up_mid: int) -> dict | None:
    """官方AI总结。返回 {'summary': str, 'outline': list} 或 None（含负缓存）。"""
    key = f"sum:{bvid}:{cid}"
    cached = _cache_get(key, _SUMMARY_TTL)
    if cached is not None:
        return None if cached == _NEG else cached

    keys = await _get_wbi_keys()
    if not keys:
        return None
    params = wbi_sign({"bvid": bvid, "cid": cid, "up_mid": up_mid}, *keys)
    s = await _get_session()
    try:
        async with s.get(
            "https://api.bilibili.com/x/web-interface/view/conclusion/get",
            params=params,
            headers=_headers(with_cookie=True),
        ) as r:
            data = await r.json(content_type=None)
    except Exception as e:
        print(f"[bili] AI总结失败: {type(e).__name__}: {e}")
        return None

    result = ((data.get("data") or {}).get("model_result")) or None
    if not result or not result.get("summary"):
        _cache_set(key, None)
        return None
    out = {"summary": result["summary"], "outline": result.get("outline", [])}
    _cache_set(key, out)
    return out


async def _dhash_url(url: str) -> int | None:
    import io

    try:
        s = await _get_session()
        async with s.get(
            url, headers={**_headers(), "Referer": "https://web.qq.com/"}
        ) as r:
            if r.status != 200:
                print(f"[bili] 图片下载失败 status={r.status}: {url[:60]}")
                return None
            raw = await r.read()
        img = Image.open(io.BytesIO(raw)).convert("L").resize((9, 8))
        px = list(img.getdata())
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
        return bits
    except Exception as e:
        print(f"[bili] 图片哈希失败: {type(e).__name__}: {e}")
        return None


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


import difflib


def _norm_title(t: str) -> str:
    t = re.sub(r"【[^】]*】", "", t)
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", t).lower()


async def video_from_card(title: str, preview_url: str = "") -> tuple[str, str]:
    ids = await search_video_by_title(title)
    if not ids:
        return "", ""

    want = _norm_title(title)
    first_visible, best, best_score = "", "", 0.0
    infos: dict[str, dict] = {}
    for vid in ids[:8]:
        info = await get_video_info(vid)
        if not info:
            continue
        infos[vid] = info
        if not first_visible:
            first_visible = vid
        got = _norm_title(info["title"])
        if got == want:
            return vid, "unique"  # 一级：标题完全一致
        score = difflib.SequenceMatcher(None, got, want).ratio()
        if score > best_score:
            best, best_score = vid, score

    # 二级：封面哈希消歧（QQ缓存封面 vs 候选封面）
    if preview_url and len(infos) > 1:
        target = await _dhash_url(preview_url)
        if target is not None:
            b_hash, b_vid, b_d = None, "", 999
            for vid, info in infos.items():
                h = await _dhash_url(info["cover"])
                if h is None:
                    continue
                d = _hamming(target, h)
                if d < b_d:
                    b_hash, b_vid, b_d = h, vid, d
            print(f"[bili] 封面哈希: 最佳={b_vid[:12] if b_vid else '无'}, 距离={b_d}")
            if b_vid and b_d <= 10:
                return b_vid, "hashed"

    if best and best_score >= 0.6:
        return best, "matched"
    if first_visible:
        return first_visible, "guessed"
    return "", ""


def _fmt_ts(sec: int) -> str:
    return f"{sec // 60:02d}:{sec % 60:02d}"


async def build_video_block(vid: str, p: int = 1) -> str:
    """组装注入 prompt 的视频信息块。失败时返回提示块。"""
    info = await get_video_info(vid)
    if not info:
        return f"【系统提示】群友分享了B站视频（{vid}），但视频信息获取失败，请群友检查链接。"

    pages = info["pages"]
    page = pages[p - 1] if 1 <= p <= len(pages) else pages[0]
    multi = len(pages) > 1

    lines = [
        f"【视频分享】B站视频《{info['title']}》"
        + (f"（第{page['page']}P：{page['part'][:24]}）" if multi else "")
        + f"，UP主：{info['up_name']}"
    ]
    summary = await get_ai_summary(info["bvid"], page["cid"], info["up_mid"])
    if summary:
        lines.append(f"【AI总结】{summary['summary'][:300]}")
        outline = summary.get("outline") or []
        if outline:
            lines.append("【章节要点】")
            count = 0
            for section in outline:
                for po in section.get("part_outline", []):
                    if count >= 8:
                        break
                    lines.append(
                        f"- {_fmt_ts(po.get('timestamp', 0))} {str(po.get('content', '')).strip()[:60]}"
                    )
                    count += 1
        lines.append(
            "根据以上总结自然地回应（点评、复述亮点、吐槽标题），不要说你亲自看了视频。"
        )
    else:
        lines.append("（该视频暂无AI总结，只能基于标题和分P信息回应。）")
    return "\n".join(lines)
