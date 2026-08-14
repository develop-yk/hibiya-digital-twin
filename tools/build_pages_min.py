#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CityGML から自前変換した 3D Tiles で docs/ の catalog.json を作る。

配布の 3D Tiles（テクスチャ付き LOD2 は同じ範囲で 194MB）ではなく、
`tools/citygml2tiles.py` で CityGML から起こしたタイルを公開に使う。
同じ範囲・同じ建物で **LOD2 が 33MB、LOD1 が 3MB** まで落ちる。

前提（この順に実行しておく）:

    python3 tools/citygml2tiles.py --input input/hibiya \\
        --output docs/tiles/hibiya/bldg_lod2 \\
        --bbox 139.7520,35.6680,139.7700,35.6810 --cell 250
    python3 tools/citygml2tiles.py --input input/hibiya \\
        --output docs/tiles/hibiya/bldg_lod1 \\
        --bbox 139.7500,35.6667,139.7750,35.6833 --cell 300 --lod 1
    python3 tools/refine_tiles.py docs/tiles/hibiya/bldg_lod2 docs/tiles/hibiya/bldg_lod1

そのうえで:

    python3 tools/build_pages_min.py
"""

from __future__ import annotations

import glob
import json
import math
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
AREA = "hibiya"

# 出力する順に並べる（id, ラベル, 既定ON）
LAYERS = [
    ("bldg_lod2", "建築物 LOD2（屋根形状）", True),
    ("bldg_lod1", "建築物 LOD1（簡易形状・広域）", False),
]

# citygml2tiles.py が書き出すバッチテーブルの属性名
PROPS = {
    "name": "name",
    "usage": "usage",
    "cls": None,          # クラスは出力していない
    "height": "height",
    "storeys": "storeys",
    "zone": None,         # 用途地域は CityGML の bldg 名前空間に無い
}

INFO_PROPS = [
    ["name", "名称"],
    ["usage", "用途"],
    ["height", "計測高さ", "m"],
    ["storeys", "地上階数", "階"],
    ["lod", "LOD"],
    ["gml_id", "建物ID"],
]


def dir_bytes(d):
    return sum(os.path.getsize(p) for p in glob.glob(os.path.join(d, "tiles", "*.b3dm")))


def main():
    layers, wards_bounds = [], []
    for lid, label, on in LAYERS:
        d = os.path.join(DOCS, "tiles", AREA, lid)
        ts_path = os.path.join(d, "tileset.json")
        if not os.path.exists(ts_path):
            print("  – %-12s 未生成（スキップ）" % lid)
            continue
        with open(ts_path, encoding="utf-8") as fh:
            ts = json.load(fh)
        region = ts["root"]["boundingVolume"]["region"]
        wards_bounds.append(region)
        n = len(glob.glob(os.path.join(d, "tiles", "*.b3dm")))
        nb = dir_bytes(d)
        meta = {}
        mp = os.path.join(d, "meta.json")
        if os.path.exists(mp):
            with open(mp, encoding="utf-8") as fh:
                meta = json.load(fh)
        detail = "CityGML変換"
        if meta.get("buildings"):
            detail += " / %s棟" % format(meta["buildings"], ",")
        layers.append({
            "id": lid, "label": label, "category": "building",
            "color": "#d9d4cb", "defaultOn": on, "detail": detail, "tint": None,
            "tiles": n, "bytes": nb,
            "sources": [{
                "ward": AREA,
                "url": "tiles/%s/%s/tileset.json" % (AREA, lid),
                "tiles": n, "bytes": nb, "region": region,
            }],
        })
        print("  ✓ %-12s %3d タイル %7.1f MB  %s" % (lid, n, nb / 1048576, detail))

    if not layers:
        print("タイルが1つも見つかりません。先に citygml2tiles.py を実行してください。")
        return 1

    b = [min(r[0] for r in wards_bounds), min(r[1] for r in wards_bounds),
         max(r[2] for r in wards_bounds), max(r[3] for r in wards_bounds)]
    bounds = [math.degrees(b[0]), math.degrees(b[1]), math.degrees(b[2]), math.degrees(b[3])]

    catalog = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "static": True,
        "textured": False,
        # citygml2tiles.py は CityGML の標高(T.P.)をそのまま使う。
        # 地理院標高タイルも T.P. なので、ジオイド補正は 0 でぴたりと合う。
        "geoidOffset": 0.0,
        "props": PROPS,
        "infoProps": INFO_PROPS,
        "floodDepthProperties": [],
        "buildingProperties": ["gml_id", "name", "usage", "height", "storeys", "lod"],
        "wards": [{
            "key": AREA, "code": "13101", "name": "日比谷・有楽町周辺",
            "year": 2025, "zip": "CityGML から変換", "zip_bytes": 0,
            "layers": len(layers),
        }],
        "bounds": bounds,
        "layers": layers,
    }
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "catalog.json"), "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, ensure_ascii=False)
    open(os.path.join(DOCS, ".nojekyll"), "w").close()

    total = sum(l["bytes"] for l in layers)
    print("\n  catalog.json を書き出しました")
    print("  範囲 %.4f,%.4f – %.4f,%.4f" % tuple(bounds))
    print("  合計 %d レイヤ / %d タイル / %.1f MB"
          % (len(layers), sum(l["tiles"] for l in layers), total / 1048576))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
