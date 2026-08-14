#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Pages 用の静的サイトを docs/ に書き出す。

`serve.py` は ZIP を動的に配信するが、GitHub Pages は静的配信しかできない。
そこで指定範囲（既定：日比谷・有楽町周辺）のタイルだけを ZIP から取り出し、
そのままブラウザから読める素のファイルとして docs/ に並べる。

    python3 tools/build_pages.py              # 全レイヤーを書き出す
    python3 tools/build_pages.py wtr tran     # 指定レイヤーだけ
    python3 tools/build_pages.py --list       # 対象レイヤーと推定サイズを表示
    python3 tools/build_pages.py --catalog    # catalog.json だけ作り直す

範囲は BBOX を書き換えれば変えられる（west, south, east, north／度）。
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import serve                                    # noqa: E402  カタログ構築を再利用する

DOCS = os.path.join(ROOT, "docs")

# 日比谷交差点を中心に、日比谷公園・有楽町・丸の内南・皇居外苑南・霞が関東・内幸町
BBOX = (139.7520, 35.6680, 139.7700, 35.6810)

# docs/ に載せるレイヤー（この順に UI へ並ぶ）。None なら全部
INCLUDE = None
# Pages の容量と読み込み時間を抑えるため、重い割に効果の薄いレイヤーは載せない。
#   bldg_lod2 … テクスチャ付きは同じ範囲で 194MB。無地版(bldg_lod2_nt)なら
#               形状はまったく同じまま 72MB で済むので、公開版は無地版だけにする
#   bldg_lod1 … 箱型。無地LOD2があれば不要
#   brid / veg_cover / veg_tree … 粗いタイルが範囲外まで含まれ、合計 180MB 超になる
#   frn … 564ファイル 46MB。街灯・標識は遠景でほぼ見えず、リクエスト数だけ増える
#   ubld … 地下街。範囲の隅（大手町側）だけで地上からは見えない
# ここから外せばそのレイヤーも docs/ に書き出される。
EXCLUDE = {"bldg_lod1", "bldg_lod2", "brid", "veg_cover", "veg_tree", "frn", "ubld"}

BBOX_RAD = (math.radians(BBOX[0]), math.radians(BBOX[1]),
            math.radians(BBOX[2]), math.radians(BBOX[3]))


def hits(region):
    """3D Tiles の region(ラジアン) が BBOX と交差するか"""
    if not region:
        return True                             # 判定できないものは残す
    w, s, e, n = region[0], region[1], region[2], region[3]
    return not (e < BBOX_RAD[0] or w > BBOX_RAD[2] or n < BBOX_RAD[1] or s > BBOX_RAD[3])


def region_of(node):
    bv = node.get("boundingVolume") or {}
    return bv.get("region")


def prune(node):
    """BBOX に交差する部分だけを残した新しいノードを返す（無ければ None）"""
    if not hits(region_of(node)):
        return None
    out = {k: v for k, v in node.items() if k != "children"}

    content = out.get("content")
    if content:
        creg = region_of(content) or region_of(node)
        if not hits(creg):
            out.pop("content")

    kids = [k for k in (prune(c) for c in node.get("children", [])) if k]
    if kids:
        out["children"] = kids
    if "content" not in out and not kids:
        return None
    return out


def uris(node, acc=None):
    acc = [] if acc is None else acc
    c = node.get("content")
    if c and "uri" in c:
        acc.append(c["uri"])
    for ch in node.get("children", []):
        uris(ch, acc)
    return acc


def build_layer(catalog, routes, layer, dry=False):
    total_bytes, total_tiles = 0, 0
    out_sources = []
    for src in layer["sources"]:
        key = (src["ward"], layer["id"])
        if key not in routes:
            continue
        zip_path, prefix = routes[key]
        zf = zipfile.ZipFile(zip_path)
        ts = json.loads(zf.read(prefix + "tileset.json"))

        root_node = prune(ts["root"])
        if root_node is None:
            continue
        ts["root"] = root_node
        ts["geometricError"] = root_node.get("geometricError", ts.get("geometricError", 100))

        names = uris(root_node)
        outdir = os.path.join(DOCS, "tiles", src["ward"], layer["id"])
        nbytes = 0
        for u in names:
            entry = prefix + u
            try:
                nbytes += zf.getinfo(entry).file_size
            except KeyError:
                pass

        if not dry:
            os.makedirs(os.path.join(outdir, "data"), exist_ok=True)
            with open(os.path.join(outdir, "tileset.json"), "w", encoding="utf-8") as fh:
                json.dump(ts, fh, ensure_ascii=False, separators=(",", ":"))
            for u in names:
                entry = prefix + u
                dest = os.path.join(outdir, u.replace("/", os.sep))
                if os.path.exists(dest) and os.path.getsize(dest) == zf.getinfo(entry).file_size:
                    continue                                    # 途中再開に対応
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(zf.read(entry))

        total_bytes += nbytes
        total_tiles += len(names)
        out_sources.append({
            "ward": src["ward"],
            "url": "tiles/%s/%s/tileset.json" % (src["ward"], layer["id"]),
            "tiles": len(names), "bytes": nbytes,
            "region": region_of(root_node),
        })
    return out_sources, total_tiles, total_bytes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    catalog, routes = serve.build_catalog(ROOT)
    if not catalog["wards"]:
        print("PLATEAU の配布ZIP が見つかりません。", file=sys.stderr)
        return 1

    targets = []
    for layer in catalog["layers"]:
        if layer["id"] in EXCLUDE:
            continue
        if INCLUDE and layer["id"] not in INCLUDE:
            continue
        if args and layer["id"] not in args:
            continue
        targets.append(layer)

    dry = "--list" in flags
    catalog_only = "--catalog" in flags

    print("\n範囲: %.4f,%.4f – %.4f,%.4f （約 %.1f km × %.1f km）" % (
        BBOX[0], BBOX[1], BBOX[2], BBOX[3],
        (BBOX[2] - BBOX[0]) * 111.32 * math.cos(math.radians(BBOX[1])),
        (BBOX[3] - BBOX[1]) * 111.32))
    print("出力: %s\n" % DOCS)

    out_layers, grand_tiles, grand_bytes = [], 0, 0
    for layer in targets:
        t0 = time.time()
        srcs, tiles, nbytes = build_layer(catalog, routes, layer,
                                          dry=dry or catalog_only)
        if not srcs:
            print("  – %-28s 範囲外" % layer["id"])
            continue
        grand_tiles += tiles
        grand_bytes += nbytes
        label, default_on = layer["label"], layer["defaultOn"]
        if layer["id"] == "bldg_lod2_nt" and "bldg_lod2" in EXCLUDE:
            # テクスチャ版を載せない構成では、無地版が主役の建物レイヤーになる
            label, default_on = "建築物 LOD2（形状）", True
        out_layers.append({
            "id": layer["id"], "label": label, "category": layer["category"],
            "color": layer["color"], "defaultOn": default_on,
            "detail": layer["detail"], "tint": layer["tint"],
            "tiles": tiles, "bytes": nbytes, "sources": srcs,
        })
        print("  %s %-28s %5d tiles %8.1f MB  (%.1fs)" % (
            "·" if (dry or catalog_only) else "✓", layer["id"], tiles,
            nbytes / 1048576, time.time() - t0))

    print("\n  合計 %d レイヤ / %d タイル / %.1f MB" % (
        len(out_layers), grand_tiles, grand_bytes / 1048576))

    if dry:
        return 0

    # --- catalog.json（部分ビルドでも既存の内容を壊さないようマージする）---
    os.makedirs(DOCS, exist_ok=True)
    cpath = os.path.join(DOCS, "catalog.json")
    merged = {}
    if os.path.exists(cpath):
        try:
            with open(cpath, encoding="utf-8") as fh:
                for l in json.load(fh).get("layers", []):
                    merged[l["id"]] = l
        except Exception:
            pass
    for l in out_layers:
        merged[l["id"]] = l

    order = {lay["id"]: i for i, lay in enumerate(catalog["layers"])}
    layers = sorted(merged.values(), key=lambda l: order.get(l["id"], 999))

    out = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "wards": catalog["wards"],
        "layers": layers,
        "bounds": list(BBOX),
        "geoidOffset": catalog["geoidOffset"],
        "buildingProperties": catalog["buildingProperties"],
        "floodDepthProperties": catalog["floodDepthProperties"],
        "static": True,
        # テクスチャ付きの建物を含むか。含まない場合ビューアは
        #「テクスチャ」モードを出さず、既定を単色にする。
        "textured": any(l["id"] == "bldg_lod2" for l in layers),
    }
    with open(cpath, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    print("  catalog.json を書き出しました（%d レイヤ）" % len(layers))

    # GitHub Pages の Jekyll 処理を無効化する（_ 始まりのパスが消えるのを防ぐ）
    open(os.path.join(DOCS, ".nojekyll"), "w").close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
