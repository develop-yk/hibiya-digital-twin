#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CityGML の「面」地物（道路・用途地域など）を 3D Tiles に変換する。

`citygml2tiles.py` は建築物（立体）専用なので、地面に貼りつく面地物のための
変換をこちらに分けた。ジオメトリ処理は citygml2tiles.py の関数を再利用する。

    # 道路（tran）
    python3 tools/citygml2tiles_area.py --feature Road \\
        --input input/tran --output docs/tiles/hibiya/tran \\
        --bbox 139.7520,35.6680,139.7700,35.6810 --zoffset 0.4

    # 用途地域（urf）
    python3 tools/citygml2tiles_area.py --feature UseDistrict \\
        --input input/urf --output docs/tiles/hibiya/zone \\
        --bbox 139.7520,35.6680,139.7700,35.6810 --zoffset 1.0

--zoffset は地形との Z ファイティング（ちらつき）を避けるための持ち上げ量[m]。
面地物は標高そのものの高さを持つため、そのままだと地形と同一平面で重なる。

コード値（用途地域・道路種別）は PLATEAU のコードリストを引いて日本語化する。
`--codelist-base` に配布物のURLかローカルディレクトリを渡す。
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from citygml2tiles import (                                     # noqa: E402
    collect_polygons, geodetic_to_ecef, enu_up, triangulate,
    _newell_normal, build_glb, build_b3dm, MAT_INDEX, local,
)

# 地物型ごとの設定
#   lod       : 使う lodN…MultiSurface（無ければ他のLODへ自動フォールバック）
#   attrs     : (CityGMLのタグ名, 出力キー, コードリスト名 or None)
#   codelists : 出力キー -> コードリストのファイル名
FEATURES = {
    "Road": {
        "lod": 1,
        "attrs": [("function", "function", "Road_function"),
                  ("class", "class", None),
                  ("usage", "usage", None)],
    },
    "UseDistrict": {
        "lod": 1,
        "attrs": [("function", "zone", "Common_districtsAndZonesType"),
                  ("floorAreaRate", "floorAreaRate", None),
                  ("buildingCoverageRate", "buildingCoverageRate", None),
                  ("buildingHeightLimits", "heightLimit", None),
                  ("location", "location", None),
                  ("custodian", "custodian", None)],
    },
    "LandUse": {
        "lod": 1,
        "attrs": [("class", "landUse", "LandUse_class")],
    },
    # 植生。単木は lod3Geometry、植被は lod3MultiSurface / lod1MultiSolid と
    # 地物ごとに入れ物が違うので、lodN で始まる要素はすべて見る。
    "SolitaryVegetationObject": {
        "lod": 3,
        "attrs": [("class", "vegClass", "SolitaryVegetationObject_class"),
                  ("species", "species", None),
                  ("height", "height", None),
                  ("trunkDiameter", "trunkDiameter", None)],
    },
    "PlantCover": {
        "lod": 3,
        "attrs": [("class", "vegClass", "PlantCover_class"),
                  ("averageHeight", "averageHeight", None)],
    },
}


def load_codelist(base, name):
    """コードリスト {コード: 日本語} を取得。base は URL でもローカルパスでもよい。"""
    if not base:
        return {}
    src = base.rstrip("/") + "/codelists/" + name + ".xml"
    try:
        if src.startswith("http"):
            with urllib.request.urlopen(src, timeout=60) as fh:
                data = fh.read()
        else:
            with open(src, "rb") as fh:
                data = fh.read()
        root = ET.fromstring(data)
    except Exception as exc:
        print("  ! コードリスト %s を取得できません (%s)" % (name, exc))
        return {}
    out = {}
    for d in root.iter():
        if local(d.tag) != "Definition":
            continue
        code = desc = None
        for c in d:
            if local(c.tag) == "name":
                code = (c.text or "").strip()
            elif local(c.tag) == "description":
                desc = (c.text or "").strip()
        if code and desc:
            out[code] = desc
    print("  コードリスト %s: %d 件" % (name, len(out)))
    return out


def pick_surface(el, want_lod):
    """lod{want} の形状要素を優先し、無ければ他のLODを使う。

    地物型によって lodNMultiSurface / lodNGeometry / lodNMultiSolid と
    入れ物が違うため、`lod<数字>` で始まる要素を一律に候補にする。
    ただし TerrainIntersection（地面との交線）は形状ではないので除く。
    """
    by_lod = defaultdict(list)
    for d in el.iter():
        t = local(d.tag)
        if not (len(t) > 3 and t.startswith("lod") and t[3].isdigit()):
            continue
        if "TerrainIntersection" in t or "ImplicitRepresentation" in t:
            continue
        by_lod[int(t[3])].append(d)
    if not by_lod:
        return [], 0
    # 同じLODで入れ子（lod1Solid の中の lod1MultiSurface 等）を拾わないよう、
    # 最も外側の要素だけを残す
    for lod in [want_lod] + sorted(by_lod, reverse=True):
        cand = by_lod.get(lod)
        if not cand:
            continue
        outer = []
        for c in cand:
            if not any(c is not o and c in list(o.iter()) for o in cand):
                outer.append(c)
        return outer or cand, lod
    return [], 0


def main():
    ap = argparse.ArgumentParser(description="CityGML の面地物 -> 3D Tiles")
    ap.add_argument("--feature", required=True, choices=sorted(FEATURES))
    ap.add_argument("--input", required=True, help="CityGML(.gml) を含むディレクトリ")
    ap.add_argument("--output", required=True)
    ap.add_argument("--bbox", required=True, help="west,south,east,north")
    ap.add_argument("--cell", type=float, default=400.0, help="タイル1辺の概算メートル")
    ap.add_argument("--zoffset", type=float, default=0.4, help="地形とのちらつき回避の持ち上げ[m]")
    ap.add_argument("--codelist-base", default="", help="コードリストのURL/ディレクトリ")
    args = ap.parse_args()

    spec = FEATURES[args.feature]
    bbox = [float(x) for x in args.bbox.split(",")]
    codes = {}
    for _tag, key, cl in spec["attrs"]:
        if cl:
            codes[key] = load_codelist(args.codelist_base, cl)

    files = sorted(glob.glob(os.path.join(args.input, "*.gml")))
    if not files:
        print("[!] %s に .gml がありません" % args.input, file=sys.stderr)
        return 1
    print("[i] CityGML %d ファイルを読み込みます" % len(files))

    items = []
    for path in files:
        n0 = len(items)
        for _ev, el in ET.iterparse(path, events=("end",)):
            if local(el.tag) != args.feature:
                continue
            surfaces, lod = pick_surface(el, spec["lod"])
            polys = []
            for s in surfaces:
                polys.extend(collect_polygons(s))
            if not polys:
                el.clear()
                continue
            lats = [p[0] for ext, _ in polys for p in ext]
            lons = [p[1] for ext, _ in polys for p in ext]
            clat, clon = sum(lats) / len(lats), sum(lons) / len(lons)
            if not (bbox[0] <= clon <= bbox[2] and bbox[1] <= clat <= bbox[3]):
                el.clear()
                continue

            attrs = {"gml_id": el.get("{http://www.opengis.net/gml}id") or "", "lod": lod}
            for tag, key, _cl in spec["attrs"]:
                attrs[key] = None
            for d in el.iter():
                t = local(d.tag)
                txt = (d.text or "").strip()
                if not txt:
                    continue
                for tag, key, _cl in spec["attrs"]:
                    if t == tag and attrs[key] is None:
                        attrs[key] = codes.get(key, {}).get(txt, txt)
            items.append({"attrs": attrs, "polys": polys, "lat": clat, "lon": clon})
            el.clear()
        print("    %s: +%d" % (os.path.basename(path), len(items) - n0))

    if not items:
        print("[!] 対象範囲に %s がありません" % args.feature, file=sys.stderr)
        return 1
    print("[i] 対象地物: %d" % len(items))

    lat0 = sum(i["lat"] for i in items) / len(items)
    dlat = args.cell / 111320.0
    dlon = args.cell / (111320.0 * math.cos(math.radians(lat0)))
    cells = defaultdict(list)
    for it in items:
        cells[(int(math.floor(it["lat"] / dlat)), int(math.floor(it["lon"] / dlon)))].append(it)

    os.makedirs(os.path.join(args.output, "tiles"), exist_ok=True)
    keys = ["gml_id", "lod"] + [k for _t, k, _c in spec["attrs"]]
    children, total_tris = [], 0

    for (ci, cj), group in sorted(cells.items()):
        positions, normals, batchids = [], [], []
        prim_idx = defaultdict(list)
        bt = {k: [] for k in keys}

        clat = sum(i["lat"] for i in group) / len(group)
        clon = sum(i["lon"] for i in group) / len(group)
        rtc = geodetic_to_ecef(np.array([clat]), np.array([clon]), np.array([0.0]))[0]
        up = enu_up(clat, clon)

        min_lat = min_lon = 1e18
        max_lat = max_lon = -1e18
        min_h, max_h = 1e18, -1e18
        vbase = 0

        for bidx, it in enumerate(group):
            for k in keys:
                bt[k].append(it["attrs"].get(k))
            for ext, ints in it["polys"]:
                e = np.array(ext, dtype=np.float64)
                e[:, 2] += args.zoffset
                min_lat = min(min_lat, e[:, 0].min()); max_lat = max(max_lat, e[:, 0].max())
                min_lon = min(min_lon, e[:, 1].min()); max_lon = max(max_lon, e[:, 1].max())
                min_h = min(min_h, e[:, 2].min()); max_h = max(max_h, e[:, 2].max())

                ecef_ext = geodetic_to_ecef(e[:, 0], e[:, 1], e[:, 2])
                ecef_ints = []
                for h in ints:
                    if len(h) < 3:
                        continue
                    a = np.array(h, dtype=np.float64)
                    a[:, 2] += args.zoffset
                    ecef_ints.append(geodetic_to_ecef(a[:, 0], a[:, 1], a[:, 2]))

                verts, tris = triangulate(ecef_ext, ecef_ints)
                if verts is None or not tris:
                    continue
                nrm = _newell_normal(verts)
                if nrm is None:
                    continue
                # 面地物は上を向かせる（裏返っていたら反転）
                if float(np.dot(nrm, up)) < 0:
                    nrm = -nrm
                    tris = [(t[0], t[2], t[1]) for t in tris]

                rel = verts - rtc
                gl = np.stack([rel[:, 0], rel[:, 2], -rel[:, 1]], axis=-1)
                gn = np.array([nrm[0], nrm[2], -nrm[1]], dtype=np.float64)
                positions.append(gl)
                normals.append(np.tile(gn, (len(gl), 1)))
                batchids.append(np.full(len(gl), bidx, dtype=np.float64))
                for t in tris:
                    prim_idx[MAT_INDEX["ground"]].extend(
                        [t[0] + vbase, t[1] + vbase, t[2] + vbase])
                vbase += len(gl)

        if not positions:
            continue
        P = np.concatenate(positions).astype(np.float32)
        N = np.concatenate(normals).astype(np.float32)
        B = np.concatenate(batchids).astype(np.float32)
        prim = {k: np.array(v, dtype=np.uint32) for k, v in prim_idx.items()}
        total_tris += sum(len(v) for v in prim.values()) // 3

        uri = "tiles/tile_%d_%d.b3dm" % (ci, cj)
        with open(os.path.join(args.output, uri), "wb") as fh:
            fh.write(build_b3dm(build_glb(P, N, B, prim), rtc, bt))

        children.append({
            "boundingVolume": {"region": [
                math.radians(min_lon), math.radians(min_lat),
                math.radians(max_lon), math.radians(max_lat),
                min_h - 2.0, max_h + 2.0]},
            "geometricError": 0.0, "refine": "ADD", "content": {"uri": uri},
        })

    regions = [c["boundingVolume"]["region"] for c in children]
    root_region = [min(r[0] for r in regions), min(r[1] for r in regions),
                   max(r[2] for r in regions), max(r[3] for r in regions),
                   min(r[4] for r in regions), max(r[5] for r in regions)]
    tileset = {
        "asset": {"version": "1.0", "tilesetVersion": "hibiya-digital-twin/1.0"},
        "properties": {}, "geometricError": 500.0,
        "root": {"boundingVolume": {"region": root_region},
                 "geometricError": 120.0, "refine": "ADD", "children": children},
    }
    with open(os.path.join(args.output, "tileset.json"), "w", encoding="utf-8") as fh:
        json.dump(tileset, fh, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.output, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"feature": args.feature, "features": len(items),
                   "tiles": len(children), "triangles": total_tris,
                   "zoffset": args.zoffset}, fh, ensure_ascii=False, indent=2)

    print("[✓] 出力完了: %s/tileset.json  (%d タイル / %d 地物 / %s 三角形)"
          % (args.output, len(children), len(items), format(total_tris, ",")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
