#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HIBIYA DIGITAL TWIN — PLATEAU 3D Tiles ローカル配信サーバ

Project PLATEAU の「3D Tiles, MVT」配布ZIP を **展開せずに** 直接 HTTP 配信する。
配布ZIP 内の .b3dm は無圧縮(STORED)で格納されているため、
ZIP のセントラルディレクトリ経由でランダムアクセスすれば実質ゼロコストで読める。

    python3 serve.py              # → http://localhost:8080/viewer/
    python3 serve.py 9000         # ポート指定
    python3 serve.py --no-browser # ブラウザを自動で開かない

公開URL
    /                         → /viewer/ へリダイレクト
    /viewer/…                 → 静的ファイル（ビューア本体）
    /api/catalog              → 検出したタイルセットのカタログ(JSON)
    /plateau/<市区>/<レイヤ>/…  → ZIP 内の tileset.json / *.b3dm
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import math
import os
import posixpath
import re
import socketserver
import sys
import threading
import struct
import time
import urllib.parse
import webbrowser
import zipfile
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# 市区町村コード → 表示名（東京23区。未知のコードはZIP名から推定）
# --------------------------------------------------------------------------
CITY_NAMES = {
    "13101": "千代田区", "13102": "中央区", "13103": "港区", "13104": "新宿区",
    "13105": "文京区", "13106": "台東区", "13107": "墨田区", "13108": "江東区",
    "13109": "品川区", "13110": "目黒区", "13111": "大田区", "13112": "世田谷区",
    "13113": "渋谷区", "13114": "中野区", "13115": "杉並区", "13116": "豊島区",
    "13117": "北区", "13118": "荒川区", "13119": "板橋区", "13120": "練馬区",
    "13121": "足立区", "13122": "葛飾区", "13123": "江戸川区",
}

# 洪水浸水想定レイヤの河川スラッグ → 表示名
RIVER_NAMES = {
    "arakawa_kandagawa-zenpukujigawa-etc-2": "荒川水系 神田川・善福寺川ほか",
    "kandagawa-ryuiki": "神田川流域",
    "sumidagaw-shingashigawa-ryuiki": "隅田川・新河岸川流域",
    "koto-naibu": "江東内部河川",
    "arakawa-shimo": "荒川下流",
    "tamagawa": "多摩川",
}

# --------------------------------------------------------------------------
# レイヤ分類ルール
#   (正規表現, 正規化ID, 表示名, カテゴリ, 優先度, 既定ON, 色)
#   同一 正規化ID が複数LODで存在する場合は「優先度」が高い方を採用する。
# --------------------------------------------------------------------------
CAT_BUILDING = "building"
CAT_INFRA = "infra"
CAT_NATURE = "nature"
CAT_RISK = "risk"
CAT_OTHER = "other"

#   tint: テクスチャを持たないレイヤーは、この不透明度でレイヤー色に塗る（None なら素材のまま）
LAYER_RULES = [
    (r"^bldg_3dtiles_.*_lod1$",
     "bldg_lod1", "建築物 LOD1（簡易形状）", CAT_BUILDING, 10, False, "#c9c4bb", None),
    (r"^bldg_3dtiles_.*_lod2_no_texture$",
     "bldg_lod2_nt", "建築物 LOD2（無地）", CAT_BUILDING, 10, False, "#d8d3ca", None),
    (r"^bldg_3dtiles_.*_lod2$",
     "bldg_lod2", "建築物 LOD2（テクスチャ）", CAT_BUILDING, 10, True, "#ffffff", None),
    (r"^tran_3dtiles_lod(?P<lod>\d)$",
     "tran", "道路", CAT_INFRA, None, False, "#7d838c", 0.95),
    (r"^brid_3dtiles_lod(?P<lod>\d)$",
     "brid", "橋梁", CAT_INFRA, None, False, "#9aa3ad", 0.95),
    (r"^frn_3dtiles_lod(?P<lod>\d)$",
     "frn", "都市設備（信号・標識・街灯ほか）", CAT_INFRA, None, False, "#b7a98a", None),
    (r"^ubld_3dtiles_lod(?P<lod>\d)$",
     "ubld", "地下街・地下埋設物", CAT_INFRA, None, False, "#a68bbf", None),
    (r"^veg_SolitaryVegetationObject_3dtiles_lod(?P<lod>\d)$",
     "veg_tree", "樹木（単木）", CAT_NATURE, None, False, "#5f9e58", None),
    (r"^veg_PlantCover_3dtiles_lod(?P<lod>\d)$",
     "veg_cover", "植被", CAT_NATURE, None, False, "#6fae63", None),
    (r"^wtr_3dtiles_lod(?P<lod>\d)$",
     "wtr", "水部（河川・堀）", CAT_NATURE, None, True, "#2f6f9e", 0.80),
    (r"^htd_.*_3dtiles(_no_texture)?$",
     "htd", "高潮浸水想定", CAT_RISK, 10, False, "#c05fa8", 0.50),
    (r"^lsld.*_3dtiles.*$",
     "lsld", "土砂災害警戒区域", CAT_RISK, 10, False, "#c98b3a", 0.50),
]

# 橋梁は LOD3 が極小範囲しか無いため LOD2 を最優先にする
LOD_PRIORITY = {
    "brid": {"1": 1, "2": 3, "3": 2},
}

FLOOD_RE = re.compile(r"^fld_(?P<admin>[a-z]+)_(?P<river>.+?)_3dtiles_l(?P<scale>\d)(?:_no_texture)?$")

SCALE_LABEL = {"1": "L1 計画規模", "2": "L2 想定最大規模"}
FLOOD_COLORS = ["#2f7fd0", "#3f9ad0", "#4f6fd0", "#5f8fb8", "#3fa0b0"]


# --------------------------------------------------------------------------
# カタログ構築
# --------------------------------------------------------------------------
class Layer:
    __slots__ = ("lid", "label", "category", "color", "default_on",
                 "priority", "detail", "tint", "sources")

    def __init__(self, lid, label, category, color, default_on, priority, detail, tint):
        self.lid = lid
        self.label = label
        self.category = category
        self.color = color
        self.default_on = default_on
        self.priority = priority
        self.detail = detail
        self.tint = tint
        self.sources = {}          # ward_key -> source dict


def classify(suffix):
    """ZIP 内ディレクトリ名（`..._op_` 以降）を正規化レイヤ情報に変換する。"""
    m = FLOOD_RE.match(suffix)
    if m:
        river = m.group("river")
        scale = m.group("scale")
        name = RIVER_NAMES.get(river, river.replace("-", "・"))
        lid = "fld_%s_l%s" % (re.sub(r"[^0-9a-zA-Z]+", "_", river).strip("_"), scale)
        color = FLOOD_COLORS[zlib.crc32(river.encode()) % len(FLOOD_COLORS)]
        label = "洪水浸水想定：%s" % name
        return lid, label, CAT_RISK, color, False, int(scale), SCALE_LABEL.get(scale, ""), 0.50

    for pattern, lid, label, cat, prio, on, color, tint in LAYER_RULES:
        m = re.match(pattern, suffix)
        if not m:
            continue
        detail = ""
        if prio is None:
            lod = (m.groupdict().get("lod") or "1")
            prio = LOD_PRIORITY.get(lid, {}).get(lod, int(lod))
            detail = "LOD%s" % lod
        return lid, label, cat, color, on, prio, detail, tint
    return None


def sample_batch_keys(zf, entries, limit=10):
    """b3dm のバッチテーブル(JSON)だけを拾い読みして属性名の集合を返す。

    b3dm ヘッダ(28B) に各ブロック長が入っているので、テクスチャを含む
    ジオメトリ本体は読まずに済む。
    """
    keys = set()
    picks = [n for n in entries if n.endswith(".b3dm")]
    if not picks:
        return keys
    step = max(1, len(picks) // limit)
    for name in picks[::step][:limit]:
        try:
            with zf.open(name) as fh:
                head = fh.read(28)
                if len(head) < 28 or head[:4] != b"b3dm":
                    continue
                _, _, _, ftjl, ftbl, btjl, _ = struct.unpack("<4sIIIIII", head)
                skip = ftjl + ftbl
                while skip > 0:                       # featureTable を読み飛ばす
                    skip -= len(fh.read(min(skip, 65536)))
                blob = fh.read(btjl)
            keys.update(json.loads(blob).keys())
        except Exception:
            continue
    return keys


def build_catalog(root):
    """リポジトリ配下の PLATEAU 3D Tiles 配布ZIP を走査してカタログを作る。"""
    import glob as _glob
    zips = []
    for pat in ("*_3dtiles_mvt_*.zip", "*/*_3dtiles_mvt_*.zip", "*/*/*_3dtiles_mvt_*.zip"):
        zips.extend(_glob.glob(os.path.join(root, pat)))
    # `.` / `_` で始まるフォルダ（作業用・退避用）は走査対象から外す
    def visible(path):
        rel = os.path.relpath(path, root)
        return not any(part.startswith((".", "_")) for part in rel.split(os.sep)[:-1])
    zips = sorted(p for p in set(zips) if visible(p))

    wards = {}
    layers = {}
    routes = {}          # (ward_key, layer_id) -> (zip_path, dir_prefix)
    bounds = None
    bldg_keys = set()    # 建築物レイヤに実在する属性名（着色UIの構築に使う）
    sampled_wards = set()

    for zp in zips:
        base = os.path.basename(zp)
        m = re.match(r"^(?P<code>\d{5})_(?P<slug>[a-z0-9\-]+)_.*?(?P<year>\d{4})_3dtiles", base)
        if not m:
            continue
        code, slug, year = m.group("code"), m.group("slug"), m.group("year")
        ward_key = "%s_%s" % (code, slug)
        ward_name = CITY_NAMES.get(code) or slug.replace("-", " ").title()

        try:
            zf = zipfile.ZipFile(zp)
        except Exception as exc:                                   # 壊れたZIPは飛ばす
            print("  ! 読み込めません: %s (%s)" % (base, exc), file=sys.stderr)
            continue

        names = zf.namelist()
        by_dir = {}
        for n in names:
            top = n.split("/", 1)[0]
            if "_3dtiles" not in top:
                continue
            by_dir.setdefault(top, []).append(n)

        found = 0
        for dirname, entries in sorted(by_dir.items()):
            ts_name = dirname + "/tileset.json"
            if ts_name not in entries:
                continue
            suffix = dirname.split("_op_", 1)[1] if "_op_" in dirname else dirname
            info = classify(suffix)
            if not info:
                lid = re.sub(r"[^0-9a-zA-Z]+", "_", suffix).strip("_")
                info = (lid, suffix, CAT_OTHER, "#9aa7b4", False, 1, "", None)
            lid, label, cat, color, on, prio, detail, tint = info

            try:
                ts = json.loads(zf.read(ts_name))
            except Exception:
                continue
            region = (ts.get("root", {}).get("boundingVolume", {}) or {}).get("region")

            data_entries = [n for n in entries if n.endswith(".b3dm") or n.endswith(".glb")]
            nbytes = 0
            for n in data_entries:
                try:
                    nbytes += zf.getinfo(n).file_size
                except KeyError:
                    pass

            layer = layers.get(lid)
            if layer is None:
                layer = layers[lid] = Layer(lid, label, cat, color, on, prio, detail, tint)
            # 同一レイヤで LOD 違い（や同一市区の重複ZIP）がある場合は、
            # 優先度 → 収録タイル数 の順で良い方を残す
            prev = layer.sources.get(ward_key)
            if prev and (prev["priority"], prev["tiles"]) >= (prio, len(data_entries)):
                continue
            if prio > layer.priority:
                layer.priority = prio
                layer.detail = detail

            layer.sources[ward_key] = {
                "ward": ward_key,
                "url": "/plateau/%s/%s/tileset.json" % (ward_key, lid),
                "tiles": len(data_entries),
                "bytes": nbytes,
                "region": region,
                "priority": prio,
                "source_dir": dirname,
            }
            routes[(ward_key, lid)] = (zp, dirname + "/")
            found += 1

            # 属性名は建築物レイヤーから採取する。浸水想定の列名は市区ごとに
            # （＝流域ごとに）違うので、市区単位で1レイヤーずつ必ず見る。
            # テクスチャ版が無い構成でも取りこぼさないよう LOD/無地は問わない。
            if cat == CAT_BUILDING and ward_key not in sampled_wards:
                keys = sample_batch_keys(zf, data_entries)
                if keys:
                    bldg_keys |= keys
                    sampled_wards.add(ward_key)

            if region and cat == CAT_BUILDING:
                w, s, e, n_, h0, h1 = region
                b = [math.degrees(w), math.degrees(s), math.degrees(e), math.degrees(n_)]
                bounds = b if bounds is None else [
                    min(bounds[0], b[0]), min(bounds[1], b[1]),
                    max(bounds[2], b[2]), max(bounds[3], b[3])]

        wards[ward_key] = {
            "key": ward_key, "code": code, "name": ward_name,
            "year": int(year), "zip": os.path.relpath(zp, root),
            "zip_bytes": os.path.getsize(zp), "layers": found,
        }
        print("  ✓ %s（%s） %2d レイヤ  ← %s" % (ward_name, code, found, os.path.relpath(zp, root)))

    cat_order = {CAT_BUILDING: 0, CAT_INFRA: 1, CAT_NATURE: 2, CAT_RISK: 3, CAT_OTHER: 4}
    layer_list = []
    for layer in sorted(layers.values(), key=lambda x: (cat_order.get(x.category, 9), x.lid)):
        srcs = sorted(layer.sources.values(), key=lambda s: s["ward"])
        layer_list.append({
            "id": layer.lid, "label": layer.label, "category": layer.category,
            "color": layer.color, "defaultOn": layer.default_on,
            "detail": layer.detail, "tint": layer.tint,
            "tiles": sum(s["tiles"] for s in srcs),
            "bytes": sum(s["bytes"] for s in srcs),
            "sources": srcs,
        })

    flood_props = sorted(k for k in bldg_keys if k.endswith("_浸水深"))
    catalog = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "wards": [wards[k] for k in sorted(wards)],
        "layers": layer_list,
        "bounds": bounds,
        # PLATEAU 配布 3D Tiles の高さは楕円体高。地理院DEM(標高)に足す補正値。
        "geoidOffset": 36.6,
        "buildingProperties": sorted(bldg_keys),
        "floodDepthProperties": flood_props,
    }
    return catalog, routes


# --------------------------------------------------------------------------
# ZIP リーダー（スレッドごとにハンドルを持つ）
# --------------------------------------------------------------------------
class ZipPool:
    def __init__(self):
        self._local = threading.local()

    def get(self, path):
        cache = getattr(self._local, "cache", None)
        if cache is None:
            cache = self._local.cache = {}
        zf = cache.get(path)
        if zf is None:
            zf = cache[path] = zipfile.ZipFile(path)
        return zf


POOL = ZipPool()
CATALOG = {"wards": [], "layers": []}
ROUTES = {}
CATALOG_BYTES = b"{}"

MIME = {
    ".b3dm": "application/octet-stream",
    ".i3dm": "application/octet-stream",
    ".pnts": "application/octet-stream",
    ".cmpt": "application/octet-stream",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".json": "application/json",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".wasm": "application/wasm",
    ".md": "text/markdown; charset=utf-8",
}


class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "HibiyaDigitalTwin/2.0"
    protocol_version = "HTTP/1.1"

    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, **MIME}

    # ---------------------------------------------------------------- utils
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_bytes(self, payload, ctype, cache="no-store", etag=None):
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _fail(self, code, message):
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # ----------------------------------------------------------------- main
    def do_GET(self):
        try:
            self._route()
        except (BrokenPipeError, ConnectionResetError):
            pass                                   # ブラウザ側のキャンセルは無視

    def do_HEAD(self):
        self.do_GET()

    def _route(self):
        path = urllib.parse.urlparse(self.path).path
        path = urllib.parse.unquote(path)

        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/viewer/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if path == "/api/catalog":
            self._send_bytes(CATALOG_BYTES, "application/json; charset=utf-8",
                             cache="no-store")
            return

        if path.startswith("/plateau/"):
            self._serve_zip(path)
            return

        return super().do_GET() if self.command == "GET" else super().do_HEAD()

    def _serve_zip(self, path):
        parts = path.split("/")            # ['', 'plateau', ward, layer, ...rest]
        if len(parts) < 5:
            return self._fail(404, "not found")
        ward, layer = parts[2], parts[3]
        rest = "/".join(parts[4:])
        if ".." in rest or rest.startswith("/"):
            return self._fail(400, "bad path")

        route = ROUTES.get((ward, layer))
        if route is None:
            return self._fail(404, "unknown tileset: %s/%s" % (ward, layer))
        zip_path, prefix = route
        entry = prefix + rest

        try:
            zf = POOL.get(zip_path)
            info = zf.getinfo(entry)
            payload = zf.read(entry)
        except KeyError:
            return self._fail(404, "entry not found: %s" % rest)
        except Exception as exc:
            return self._fail(500, "zip read error: %s" % exc)

        ext = posixpath.splitext(entry)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        etag = '"%s"' % hashlib.md5(
            ("%s|%s|%d|%s" % (zip_path, entry, info.file_size, info.CRC)).encode()
        ).hexdigest()
        # ZIP の中身は不変なので長期キャッシュしてよい
        self._send_bytes(payload, ctype, cache="public, max-age=604800", etag=etag)

    # 全レスポンス共通で CORS を付与する
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt, *args):
        msg = fmt % args
        if " 404 " in msg or " 500 " in msg:
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), msg))


class ThreadedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128


def main():
    ap = argparse.ArgumentParser(description="PLATEAU 3D Tiles ローカル配信サーバ")
    ap.add_argument("port", nargs="?", type=int, default=8080)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--host", default="")
    args = ap.parse_args()

    global CATALOG, ROUTES, CATALOG_BYTES
    print("PLATEAU 配布ZIP を走査しています…")
    t0 = time.time()
    CATALOG, ROUTES = build_catalog(ROOT)
    CATALOG_BYTES = json.dumps(CATALOG, ensure_ascii=False).encode("utf-8")

    n_layers = len(CATALOG["layers"])
    n_tiles = sum(l["tiles"] for l in CATALOG["layers"])
    n_bytes = sum(l["bytes"] for l in CATALOG["layers"])
    print("  → %d 市区 / %d レイヤ / %d タイル / %.1f GB  (%.2f 秒)"
          % (len(CATALOG["wards"]), n_layers, n_tiles, n_bytes / 1073741824, time.time() - t0))
    if not CATALOG["wards"]:
        print("\n  ⚠ 3D Tiles の配布ZIP が見つかりません。")
        print("    `<市区名>/<コード>_<市区>_..._3dtiles_mvt_1_op.zip` を配置してください。\n")

    os.chdir(ROOT)
    with ThreadedServer((args.host, args.port), Handler) as httpd:
        url = "http://localhost:%d/viewer/" % args.port
        print("\nHIBIYA DIGITAL TWIN  →  %s" % url)
        print("Ctrl+C で停止\n")
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n停止しました")


if __name__ == "__main__":
    main()
