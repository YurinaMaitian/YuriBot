"""
B站分享解析探针：搜索反查 + 封面比对 + 分P结构检查
用法:
    python3 test_bili.py "SESSDATA=xxx; bili_jct=xxx; buvid3=xxx" [搜索关键词]
或不带参数，直接改下面 COOKIE / KEYWORD 常量
"""

import sys
import json
import time
import hashlib
import re
import urllib.parse

import requests
from PIL import Image

# ========== 填这里 ==========
COOKIE = "d4ab3eaf%2C1804160012%2C1302f%2A92CjAHN8y_kkF64uI9GpNTLkTmUrXQ7qzc7PN_lNEQBw4BoZncQQMRMPXkzAp2PBONF-cSVjg5UFZ3eVRKSUlqNGd2c2NteklHT1dVRng2YnJVVUthYnRQMGtRQ0FVdnRIblJIWEdId29scnJaUloxS2RKaXZVQWJ5dFBNMHF3S003WEFmeWQ5T3BnIIEC"
KEYWORD = "【教学】b站视频 手机怎么分集分p ?"
QQ_PREVIEW_URL = "https://qq.ugcimg.cn/v1/hcgjki0cgabvlfkiqsbdmi44kik7h2rsbuecvo7sslveu59fok2b9v2dface6ooe2dibfb411g5mqgj673gpjr7j997gim4u81hr1b83ei03afe5nf0aqsb3hi05f0dt17a3j4bkp98hf8nbhbhc5g2du8/e9vf2tsqr6j29hl2kodb3vahlc"
# ↑ 你日志里那张"分P教学"视频的卡片封面（QQ缓存版）
# ============================

if len(sys.argv) > 1:
    COOKIE = sys.argv[1]
if len(sys.argv) > 2:
    KEYWORD = sys.argv[2]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
    "Cookie": COOKIE,
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _warmup():
    """访问首页拿 buvid3/b_nut 等访客 cookie（搜索接口强制要求）"""
    try:
        SESSION.get("https://www.bilibili.com", timeout=10)
        print(f"[预热] cookies: {list(SESSION.cookies.keys())}")
    except Exception as e:
        print(f"[预热失败] {e}")


MIXIN_TAB = [
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


def get_wbi_keys():
    r = SESSION.get(
        "https://api.bilibili.com/x/web-interface/nav", headers=HEADERS, timeout=10
    )
    data = r.json()
    if data.get("code") != 0:
        print(f"[nav失败] {data}")
        sys.exit(1)
    wbi = data["data"]["wbi_img"]
    img_key = wbi["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub_key = wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0]
    print(f"[nav] 登录态: {data['data'].get('isLogin')}, wbi keys 获取成功")
    return img_key, sub_key


def wbi_sign(params, img_key, sub_key):
    mixin = "".join((img_key + sub_key)[i] for i in MIXIN_TAB)[:32]
    params = {
        k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()
    }
    params["wts"] = int(time.time())
    query = urllib.parse.urlencode(sorted(params.items()))
    params["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return params


def search(keyword, img_key, sub_key, w_webid=""):
    params = {"search_type": "video", "keyword": keyword}
    if w_webid:
        params["w_webid"] = w_webid
    params = wbi_sign(params, img_key, sub_key)

    r = SESSION.get(
        "https://api.bilibili.com/x/web-interface/search/type",
        params=params,
        timeout=10,
    )
    try:
        data = r.json()
    except Exception:
        print(f"[搜索返回非JSON] status={r.status_code}, body: {r.text[:300]!r}")
        return []
    if data.get("code") != 0:
        print(f"[搜索失败] code={data.get('code')}, msg={data.get('message')}")
        return []
    results = data.get("data", {}).get("result", []) or []
    print(f"[搜索] 命中 {len(results)} 条")
    for i, v in enumerate(results[:5]):
        title = (
            v.get("title", "").replace('<em class="keyword">', "").replace("</em>", "")
        )
        print(
            f"  {i + 1}. {v.get('bvid')} | {title} | UP: {v.get('author')} | 封面: {v.get('pic')}"
        )
    return results


def view(bvid):
    r = SESSION.get(
        "https://api.bilibili.com/x/web-interface/view",
        params={"bvid": bvid},
        headers=HEADERS,
        timeout=10,
    )
    data = r.json()
    if data.get("code") != 0:
        print(f"[view失败] {data}")
        return None
    d = data["data"]
    print(f"[view] {d['bvid']} | {d['title']} | 封面: {d['pic']}")
    print(f"[view] 分P数: {len(d.get('pages', []))}")
    for p in d.get("pages", []):
        print(
            f"  P{p.get('page')} | cid={p.get('cid')} | part名: {p.get('part')} | first_frame: {p.get('first_frame')}"
        )
    print(f"[view] 原始JSON已存 view_{bvid}.json")
    with open(f"view_{bvid}.json", "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return d


def dhash(path_or_url, name):
    img = (
        Image.open(
            path_or_url
            if isinstance(path_or_url, str) and not path_or_url.startswith("http")
            else _download(path_or_url, name)
        )
        .convert("L")
        .resize((9, 8))
    )
    px = list(img.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def _download(url, name):
    r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=15)
    path = f"/tmp/{name}"
    with open(path, "wb") as f:
        f.write(r.content)
    print(f"[下载] {name}: {len(r.content)} bytes")
    return path


def hamming(a, b):
    return bin(a ^ b).count("1")


APP_KEYS = [
    ("1d8b6e7d45233436", "560c52ccd288fed045859ed18bffd973", "android", "6240300"),
    (
        "4409e2ce8ffd12b8",
        "59b43e04ad6965f34319062b478f83dd",
        "android_tv_yst",
        "105500",
    ),  # TV端key
]


def app_search(keyword: str):
    import re as _re

    clean_kw = _re.sub(r"【[^】]*】", "", keyword).strip()
    for kw in dict.fromkeys([keyword, clean_kw]):  # 原词+清洗词
        for appkey, secret, mobi, build in APP_KEYS:
            params = {
                "build": build,
                "mobi_app": mobi,
                "platform": "android",
                "appkey": appkey,
                "ts": int(time.time()),
                "keyword": kw,
                "search_type": "video",
            }
            params["sign"] = hashlib.md5(
                (urllib.parse.urlencode(sorted(params.items())) + secret).encode()
            ).hexdigest()
            r = SESSION.get(
                "https://app.bilibili.com/x/v2/search/type", params=params, timeout=10
            )
            try:
                data = r.json()
            except Exception:
                print(f"[APP搜索非JSON|{mobi}] {r.status_code}")
                continue
            d = data.get("data", {}) or {}
            items = d.get("items", []) or []
            print(
                f"[APP搜索|{mobi}|{kw[:15]}] 命中{len(items)}, keyword回显={d.get('keyword')!r}"
            )
            if items:
                print(f"  [首条] {json.dumps(items[0], ensure_ascii=False)[:400]}")
                return items
    return []


def suggest_search(keyword: str):
    import re as _re

    clean_kw = _re.sub(r"【[^】]*】", "", keyword).strip()
    # 短词：取标题里最后一段（通常是辨识度的核心词）
    candidates = [keyword, clean_kw, clean_kw[-12:] if len(clean_kw) > 12 else clean_kw]
    for term in dict.fromkeys(candidates):
        if not term:
            continue
        r = SESSION.get(
            "https://s.search.bilibili.com/main/suggest",
            params={"term": term, "main_ver": "v1"},
            timeout=10,
        )
        try:
            data = r.json()
        except Exception:
            print(f"[建议非JSON] {r.status_code}")
            continue
        print(f"[建议|{term!r}] {json.dumps(data, ensure_ascii=False)[:500]}")


def bing_search(keyword: str) -> list[str]:
    """Bing site搜索：解包 ck/a 重定向链接拿真实URL"""
    import base64
    import re as _re

    q = urllib.parse.quote(f'site:bilibili.com/video "{keyword}"')
    r = SESSION.get(
        f"https://www.bing.com/search?q={q}&count=10&setlang=zh-CN", timeout=10
    )
    print(f"[Bing] status={r.status_code}, 页面长度={len(r.text)}")
    bvs = _re.findall(r"BV1[a-zA-Z0-9]{9}", r.text)
    for m in _re.finditer(r'href="(https://www\.bing\.com/ck/a\?[^"]+)"', r.text):
        u = urllib.parse.parse_qs(urllib.parse.urlparse(m.group(1)).query).get(
            "u", [""]
        )[0]
        if not u:
            continue
        try:
            decoded = base64.urlsafe_b64decode(u + "=" * (-len(u) % 4)).decode(
                "utf-8", "ignore"
            )
            bvs += _re.findall(r"BV1[a-zA-Z0-9]{9}", decoded)
        except Exception:
            pass
    bvs = list(dict.fromkeys(bvs))
    print(f"[Bing site搜索] 提取到 {len(bvs)} 个BV: {bvs[:5]}")
    return bvs


def get_w_webid() -> str:
    """从B站页面的 __RENDER_DATA__ 提取 w_webid（JWT，ttl 86400）。
    依次尝试搜索页和首页——哪页埋了挖哪页"""
    for url in [
        "https://search.bilibili.com/all?keyword=test",
        "https://www.bilibili.com/",
    ]:
        try:
            r = SESSION.get(url, timeout=10)
        except Exception as e:
            print(f"[w_webid] GET失败 {url}: {e}")
            continue
        has = "RENDER_DATA" in r.text
        print(
            f"[w_webid] {url} → status={r.status_code}, 长度={len(r.text)}, 含RENDER_DATA={has}"
        )
        if not has:
            continue
        m = re.search(r'<script id="__RENDER_DATA__"[^>]*>([^<]+)</script>', r.text)
        if not m:
            continue
        data = json.loads(urllib.parse.unquote(m.group(1)))
        found: list[str] = []

        def _walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "access_id" and isinstance(v, str):
                        found.append(v)
                    else:
                        _walk(v)
            elif isinstance(o, list):
                for v in o:
                    _walk(v)

        _walk(data)
        if found:
            print(f"[w_webid] 从 {url} 获取成功")
            return found[0]
    print("[w_webid] 所有页面均未挖到")
    return ""


def sogou_search(keyword: str) -> list[str]:
    """搜狗：国内机房友好，结果页直链明文"""
    import re as _re

    q = urllib.parse.quote(f"site:bilibili.com/video {keyword}")
    r = SESSION.get(f"https://www.sogou.com/web?query={q}", timeout=10)
    print(f"[Sogou] status={r.status_code}, 长度={len(r.text)}")
    bvs = list(dict.fromkeys(_re.findall(r"BV1[a-zA-Z0-9]{9}", r.text)))
    avs = list(dict.fromkeys(_re.findall(r"/video/av(\d+)", r.text)))
    print(f"[Sogou] BV: {bvs[:5]}, AV: {avs[:5]}")
    return bvs or [f"av{a}" for a in avs]


def test_conclusion(bvid: str, cid: int, up_mid: int, img_key: str, sub_key: str):
    """官方AI总结接口：比字幕更直接的总结来源"""
    params = wbi_sign({"bvid": bvid, "cid": cid, "up_mid": up_mid}, img_key, sub_key)
    r = SESSION.get(
        "https://api.bilibili.com/x/web-interface/view/conclusion/get",
        params=params,
        timeout=10,
    )
    print(f"[AI总结] {r.text[:500]}")


def ddg_search(keyword: str) -> list[str]:
    """DuckDuckGo HTML版：无JS无验证码，对爬虫相对友好"""
    import re as _re

    q = urllib.parse.quote(f"site:bilibili.com/video {keyword}")
    r = SESSION.post("https://html.duckduckgo.com/html/", data={"q": q}, timeout=10)
    print(f"[DDG] status={r.status_code}, 页面长度={len(r.text)}")
    bvs = list(dict.fromkeys(_re.findall(r"BV1[a-zA-Z0-9]{9}", r.text)))
    print(f"[DDG site搜索] 提取到 {len(bvs)} 个BV: {bvs[:5]}")
    return bvs


if __name__ == "__main__":
    _warmup()
    img_key, sub_key = get_wbi_keys()
    w_webid = get_w_webid()

    results = search(KEYWORD, img_key, sub_key, w_webid)
    bvid = results[0].get("bvid") if results else ""

    if not bvid:
        for engine in (bing_search, sogou_search):
            try:
                ids = engine(KEYWORD)
                if ids:
                    bvid = ids[0]
                    break
            except Exception as e:
                print(f"[{engine.__name__}异常] {e}")

    if not bvid:
        bvs = bing_search(KEYWORD)
        bvid = bvs[0] if bvs else ""

    if bvid:
        info = view(bvid)  # view 里已经打印了分P结构和封面
        if info:
            cid = info["pages"][0]["cid"]
            up_mid = info["owner"]["mid"]
            test_conclusion(bvid, cid, up_mid, img_key, sub_key)
    if not bvid:
        try:
            items = app_search(KEYWORD)
            if items:
                import re as _re

                m = _re.search(r"BV1[a-zA-Z0-9]{9}", json.dumps(items[0]))
                bvid = m.group(0) if m else ""
        except Exception as e:
            print(f"[APP搜索异常] {e}")
    if not bvid:
        try:
            suggest_search(KEYWORD)
        except Exception as e:
            print(f"[建议接口异常] {e}")
    if not bvid:
        try:
            bvs = bing_search(KEYWORD) or ddg_search(KEYWORD)
            bvid = bvs[0] if bvs else ""
        except Exception as e:
            print(f"[搜索引擎异常] {e}")
