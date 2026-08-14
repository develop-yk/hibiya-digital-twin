#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
citygml2tiles.py
PLATEAU (Project PLATEAU / MLIT) の CityGML 建築物モデル (bldg) を
Cesium / WebGL で読める 3D Tiles 1.0 (b3dm) に変換する。

- 入力 : PLATEAU CityGML の bldg *.gml (EPSG:6697 = JGD2011 経緯度 + 標高)
- 出力 : tileset.json + tiles/*.b3dm

依存: lxml, numpy のみ（pyproj 等は不要）

使い方:
    python3 tools/citygml2tiles.py \
        --input  input/ \
        --output data/hibiya \
        --bbox   139.7480,35.6660,139.7700,35.6820 \
        --cell   250

--bbox を省略すると入力データ全体を変換する。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
from collections import defaultdict

import numpy as np
from lxml import etree

# --------------------------------------------------------------------------
# 定数
# --------------------------------------------------------------------------

GML_NS = "http://www.opengis.net/gml"

# WGS84 / JGD2011 楕円体（差は数 cm なので同一視して扱う）
WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)

# 面の分類しきい値（ENU 上方向との内積）
ROOF_COS = 0.80
GROUND_COS = -0.80

# マテリアル定義: (name, baseColorFactor)
MATERIALS = [
    ("wall", [0.855, 0.843, 0.816, 1.0]),
    ("roof", [0.596, 0.647, 0.702, 1.0]),
    ("ground", [0.400, 0.404, 0.412, 1.0]),
]
MAT_INDEX = {"wall": 0, "roof": 1, "ground": 2}


# --------------------------------------------------------------------------
# 座標変換
# --------------------------------------------------------------------------

def geodetic_to_ecef(lat_deg, lon_deg, h):
    """緯度経度(度)+標高 -> ECEF (m)。配列対応。"""
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    sin_lat = np.sin(lat)
    cos_lat = np.cos(lat)
    n = WGS84_A / np.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + h) * cos_lat * np.cos(lon)
    y = (n + h) * cos_lat * np.sin(lon)
    z = (n * (1.0 - WGS84_E2) + h) * sin_lat
    return np.stack([x, y, z], axis=-1)


def enu_up(lat_deg, lon_deg):
    """その地点の ENU 上方向ベクトル(ECEF)。"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    return np.array(
        [math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)],
        dtype=np.float64,
    )


# --------------------------------------------------------------------------
# 多角形の三角形分割（穴あき対応 ear clipping）
# --------------------------------------------------------------------------

def _newell_normal(pts):
    n = np.zeros(3)
    m = len(pts)
    for i in range(m):
        a = pts[i]
        b = pts[(i + 1) % m]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    ln = np.linalg.norm(n)
    if ln < 1e-12:
        return None
    return n / ln


def _plane_basis(normal):
    ref = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(ref, normal)) > 0.9:
        ref = np.array([1.0, 0.0, 0.0])
    u = np.cross(ref, normal)
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    return u, v


def _signed_area(p):
    a = 0.0
    m = len(p)
    for i in range(m):
        x1, y1 = p[i]
        x2, y2 = p[(i + 1) % m]
        a += x1 * y2 - x2 * y1
    return a * 0.5


def _point_in_triangle(px, py, ax, ay, bx, by, cx, cy, eps=1e-12):
    """厳密に内部にあるときだけ True（頂点・辺上は False）。
    ブリッジで重複した頂点が耳判定を妨げないようにするため。"""
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-20:
        return False
    l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
    l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
    l3 = 1.0 - l1 - l2
    return l1 > eps and l2 > eps and l3 > eps


def _same_pt(p, q, eps=1e-12):
    return abs(p[0] - q[0]) < eps and abs(p[1] - q[1]) < eps


def _seg_cross(p1, p2, p3, p4):
    """線分 p1p2 と p3p4 が「端点を共有せずに」交差するか。"""
    def o(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if v > 1e-14:
            return 1
        if v < -1e-14:
            return -1
        return 0
    if _same_pt(p1, p3) or _same_pt(p1, p4) or _same_pt(p2, p3) or _same_pt(p2, p4):
        return False
    return o(p1, p2, p3) * o(p1, p2, p4) < 0 and o(p3, p4, p1) * o(p3, p4, p2) < 0


def _earcut(poly2d, indices):
    """反時計回りに整えた単一ループを ear clipping で三角形化。"""
    tris = []
    idx = list(range(len(poly2d)))
    guard = 0
    limit = 4 * len(poly2d) + 64
    while len(idx) > 3 and guard < limit:
        guard += 1
        clipped = False
        n = len(idx)
        for i in range(n):
            i0 = idx[(i - 1) % n]
            i1 = idx[i]
            i2 = idx[(i + 1) % n]
            ax, ay = poly2d[i0]
            bx, by = poly2d[i1]
            cx, cy = poly2d[i2]
            cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            if cross <= 1e-14:
                continue  # 凸でない / 退化
            ok = True
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                p = poly2d[j]
                if _same_pt(p, (ax, ay)) or _same_pt(p, (bx, by)) or _same_pt(p, (cx, cy)):
                    continue  # ブリッジ由来の重複頂点
                if _point_in_triangle(p[0], p[1], ax, ay, bx, by, cx, cy):
                    ok = False
                    break
            if not ok:
                continue
            tris.append((indices[i0], indices[i1], indices[i2]))
            idx.pop(i)
            clipped = True
            break
        if not clipped:
            break
    if len(idx) == 3:
        tris.append((indices[idx[0]], indices[idx[1]], indices[idx[2]]))
    return tris


def triangulate(exterior, interiors):
    """exterior/interiors: (N,3) の ECEF 点列。戻り値 (verts(M,3), tris[(i,j,k)])"""
    if len(exterior) < 3:
        return None, []
    normal = _newell_normal(exterior)
    if normal is None:
        return None, []
    u, v = _plane_basis(normal)
    origin = exterior[0]

    def to2d(pts):
        d = pts - origin
        return [(float(np.dot(p, u)), float(np.dot(p, v))) for p in d]

    verts = list(exterior)
    outer2d = to2d(exterior)
    if _signed_area(outer2d) < 0:
        outer2d.reverse()
        verts_outer = list(reversed(list(exterior)))
        verts = verts_outer
    outer_idx = list(range(len(verts)))

    ring2d = list(outer2d)
    ring_idx = list(outer_idx)

    # 穴をブリッジで外周に結合
    for hole in interiors:
        if len(hole) < 3:
            continue
        h2d = to2d(hole)
        hverts = list(hole)
        if _signed_area(h2d) > 0:  # 穴は時計回りにする
            h2d.reverse()
            hverts = list(reversed(hverts))
        base = len(verts)
        verts.extend(hverts)
        hidx = list(range(base, base + len(hverts)))

        # 穴の最も右の点からブリッジを張る
        hm = max(range(len(h2d)), key=lambda i: h2d[i][0])
        hx, hy = h2d[hm]
        cands = sorted(
            range(len(ring2d)),
            key=lambda i: (ring2d[i][0] - hx) ** 2 + (ring2d[i][1] - hy) ** 2,
        )
        best = cands[0]
        for cand in cands[:12]:
            seg_a, seg_b = ring2d[cand], (hx, hy)
            blocked = False
            for k in range(len(ring2d)):
                if _seg_cross(seg_a, seg_b, ring2d[k], ring2d[(k + 1) % len(ring2d)]):
                    blocked = True
                    break
            if not blocked:
                for k in range(len(h2d)):
                    if _seg_cross(seg_a, seg_b, h2d[k], h2d[(k + 1) % len(h2d)]):
                        blocked = True
                        break
            if not blocked:
                best = cand
                break
        bridged2d = (
            ring2d[: best + 1]
            + h2d[hm:]
            + h2d[: hm + 1]
            + ring2d[best:]
        )
        bridged_idx = (
            ring_idx[: best + 1]
            + hidx[hm:]
            + hidx[: hm + 1]
            + ring_idx[best:]
        )
        ring2d, ring_idx = bridged2d, bridged_idx

    tris = _earcut(ring2d, ring_idx)
    return np.array(verts, dtype=np.float64), tris


# --------------------------------------------------------------------------
# CityGML パース
# --------------------------------------------------------------------------

def local(tag):
    if isinstance(tag, str) and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def parse_ring(ring_el):
    """gml:LinearRing -> (N,3) lat,lon,h の配列"""
    coords = []
    for child in ring_el.iter():
        lt = local(child.tag)
        if lt == "posList" and child.text:
            vals = child.text.split()
            dim = int(ring_el.get("srsDimension") or child.get("srsDimension") or 3)
            for i in range(0, len(vals) - dim + 1, dim):
                coords.append(
                    (float(vals[i]), float(vals[i + 1]), float(vals[i + 2]) if dim >= 3 else 0.0)
                )
        elif lt == "pos" and child.text:
            vals = child.text.split()
            if len(vals) >= 2:
                coords.append(
                    (float(vals[0]), float(vals[1]), float(vals[2]) if len(vals) >= 3 else 0.0)
                )
    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return coords


def collect_polygons(el):
    """要素配下の gml:Polygon をすべて集める -> [(exterior, [interiors])]"""
    out = []
    for poly in el.iter():
        if local(poly.tag) != "Polygon":
            continue
        ext, ints = None, []
        for child in poly:
            lt = local(child.tag)
            if lt == "exterior":
                for r in child.iter():
                    if local(r.tag) == "LinearRing":
                        ext = parse_ring(r)
                        break
            elif lt == "interior":
                for r in child.iter():
                    if local(r.tag) == "LinearRing":
                        ints.append(parse_ring(r))
                        break
        if ext and len(ext) >= 3:
            out.append((ext, ints))
    return out


def pick_lod_element(building):
    """LOD2 があれば LOD2、なければ LOD1 のジオメトリ保持要素を返す。"""
    lod2, lod1 = [], []
    for el in building.iter():
        lt = local(el.tag)
        if lt.startswith("lod2"):
            lod2.append(el)
        elif lt.startswith("lod1"):
            lod1.append(el)
    if lod2:
        return lod2, 2
    return lod1, 1


def building_attributes(building):
    attrs = {
        "gml_id": building.get("{http://www.opengis.net/gml}id") or "",
        "name": "",
        "usage": "",
        "height": 0.0,
        "storeys": 0,
        "year": 0,
    }
    for el in building.iter():
        lt = local(el.tag)
        txt = (el.text or "").strip()
        if not txt:
            # 汎用属性 (gen:stringAttribute name="...")
            if lt in ("stringAttribute", "genericAttribute"):
                nm = el.get("name") or ""
                for c in el:
                    if local(c.tag) == "value" and c.text:
                        if nm in ("建物ID", "建物名称", "名称"):
                            attrs["name"] = attrs["name"] or c.text.strip()
            continue
        if lt == "name" and not attrs["name"]:
            attrs["name"] = txt
        elif lt == "usage" and not attrs["usage"]:
            attrs["usage"] = txt
        elif lt == "measuredHeight":
            try:
                attrs["height"] = float(txt)
            except ValueError:
                pass
        elif lt == "storeysAboveGround":
            try:
                attrs["storeys"] = int(txt)
            except ValueError:
                pass
        elif lt == "yearOfConstruction":
            try:
                attrs["year"] = int(txt[:4])
            except ValueError:
                pass
    return attrs


# --------------------------------------------------------------------------
# glTF / b3dm 書き出し
# --------------------------------------------------------------------------

def pad(buf, alignment=4, fill=b"\x00"):
    r = len(buf) % alignment
    if r:
        buf += fill * (alignment - r)
    return buf


def build_glb(positions, normals, batchids, prim_indices):
    """positions/normals: (N,3) float32 (Y-up, RTC相対), batchids: (N,) float32
    prim_indices: {material_index: np.array(uint32)}"""
    bin_parts = []
    offset = 0
    buffer_views = []
    accessors = []

    def add_view(data: bytes, target=None, stride=None):
        nonlocal offset
        data = pad(data, 4)
        bv = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            bv["target"] = target
        buffer_views.append(bv)
        bin_parts.append(data)
        offset += len(data)
        return len(buffer_views) - 1

    pos_bytes = positions.astype("<f4").tobytes()
    nrm_bytes = normals.astype("<f4").tobytes()
    bid_bytes = batchids.astype("<f4").tobytes()

    pos_bv = add_view(pos_bytes, 34962)
    nrm_bv = add_view(nrm_bytes, 34962)
    bid_bv = add_view(bid_bytes, 34962)

    accessors.append(
        {
            "bufferView": pos_bv,
            "componentType": 5126,
            "count": int(len(positions)),
            "type": "VEC3",
            "min": [float(x) for x in positions.min(axis=0)],
            "max": [float(x) for x in positions.max(axis=0)],
        }
    )
    accessors.append(
        {"bufferView": nrm_bv, "componentType": 5126, "count": int(len(normals)), "type": "VEC3"}
    )
    accessors.append(
        {"bufferView": bid_bv, "componentType": 5126, "count": int(len(batchids)), "type": "SCALAR"}
    )

    primitives = []
    for mat_idx, idx in sorted(prim_indices.items()):
        if len(idx) == 0:
            continue
        idx_bv = add_view(idx.astype("<u4").tobytes(), 34963)
        accessors.append(
            {
                "bufferView": idx_bv,
                "componentType": 5125,
                "count": int(len(idx)),
                "type": "SCALAR",
            }
        )
        primitives.append(
            {
                "attributes": {"POSITION": 0, "NORMAL": 1, "_BATCHID": 2},
                "indices": len(accessors) - 1,
                "material": mat_idx,
                "mode": 4,
            }
        )

    gltf = {
        "asset": {"version": "2.0", "generator": "citygml2tiles.py (PLATEAU Hibiya)"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "plateau_bldg"}],
        "meshes": [{"primitives": primitives}],
        "materials": [
            {
                "name": name,
                "doubleSided": True,
                "pbrMetallicRoughness": {
                    "baseColorFactor": color,
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.92,
                },
            }
            for name, color in MATERIALS
        ],
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": offset}],
    }

    bin_chunk = b"".join(bin_parts)
    json_chunk = pad(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), 4, b" ")
    bin_chunk = pad(bin_chunk, 4)

    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    glb = struct.pack("<III", 0x46546C67, 2, total)
    glb += struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk
    glb += struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
    return glb


def pad_to_offset(buf, base_offset, alignment=8, fill=b" "):
    """タイル先頭からの絶対オフセットが alignment の倍数になるよう末尾を詰める。"""
    r = (base_offset + len(buf)) % alignment
    if r:
        buf += fill * (alignment - r)
    return buf


def build_b3dm(glb, rtc_center, batch_table):
    ft = {"BATCH_LENGTH": len(batch_table["gml_id"]), "RTC_CENTER": list(rtc_center)}
    header_len = 28
    ft_json = pad_to_offset(
        json.dumps(ft, separators=(",", ":")).encode("utf-8"), header_len
    )
    bt_json = pad_to_offset(
        json.dumps(batch_table, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        header_len + len(ft_json),
    )
    total = header_len + len(ft_json) + len(bt_json) + len(glb)
    header = struct.pack(
        "<4sIIIIII",
        b"b3dm",
        1,
        total,
        len(ft_json),
        0,
        len(bt_json),
        0,
    )
    return header + ft_json + bt_json + glb


# --------------------------------------------------------------------------
# メイン変換
# --------------------------------------------------------------------------

def convert(input_dir, output_dir, bbox, cell_m, lod_pref):
    gml_files = []
    for root, _dirs, files in os.walk(input_dir):
        for f in files:
            if f.lower().endswith(".gml"):
                gml_files.append(os.path.join(root, f))
    gml_files.sort()
    if not gml_files:
        print(f"[!] {input_dir} に .gml が見つかりません", file=sys.stderr)
        return 1

    print(f"[i] CityGML {len(gml_files)} ファイルを読み込みます")

    buildings = []  # {attrs, polygons:[(ext,ints)], lon, lat}
    for path in gml_files:
        try:
            tree = etree.parse(path)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] パース失敗 {path}: {exc}", file=sys.stderr)
            continue
        n_before = len(buildings)
        for el in tree.getroot().iter():
            if local(el.tag) != "Building":
                continue
            lod_els, lod = pick_lod_element(el)
            if lod_pref == 1:
                lod_els = [e for e in el.iter() if local(e.tag).startswith("lod1")] or lod_els
                lod = 1
            polys = []
            for le in lod_els:
                polys.extend(collect_polygons(le))
            if not polys:
                continue
            lats = [p[0] for ext, _ in polys for p in ext]
            lons = [p[1] for ext, _ in polys for p in ext]
            clat = sum(lats) / len(lats)
            clon = sum(lons) / len(lons)
            if bbox and not (
                bbox[0] <= clon <= bbox[2] and bbox[1] <= clat <= bbox[3]
            ):
                continue
            attrs = building_attributes(el)
            attrs["lod"] = lod
            buildings.append(
                {"attrs": attrs, "polys": polys, "lon": clon, "lat": clat}
            )
        print(f"    {os.path.basename(path)}: +{len(buildings) - n_before} 棟")

    if not buildings:
        print("[!] 対象範囲に建築物がありません（--bbox を確認してください）", file=sys.stderr)
        return 1

    print(f"[i] 対象建築物: {len(buildings)} 棟")

    # --- タイル分割（緯度経度グリッド） ---
    lat0 = sum(b["lat"] for b in buildings) / len(buildings)
    dlat = cell_m / 111320.0
    dlon = cell_m / (111320.0 * math.cos(math.radians(lat0)))
    cells = defaultdict(list)
    for b in buildings:
        cells[(int(math.floor(b["lat"] / dlat)), int(math.floor(b["lon"] / dlon)))].append(b)

    os.makedirs(os.path.join(output_dir, "tiles"), exist_ok=True)

    children = []
    total_tris = 0
    tile_no = 0
    for (ci, cj), items in sorted(cells.items()):
        tile_no += 1
        positions, normals, batchids = [], [], []
        prim_idx = defaultdict(list)
        bt = {"gml_id": [], "name": [], "usage": [], "height": [], "storeys": [], "lod": []}

        # RTC 中心
        clat = sum(b["lat"] for b in items) / len(items)
        clon = sum(b["lon"] for b in items) / len(items)
        rtc = geodetic_to_ecef(np.array([clat]), np.array([clon]), np.array([0.0]))[0]
        up = enu_up(clat, clon)

        min_lat = min_lon = 1e18
        max_lat = max_lon = -1e18
        min_h, max_h = 1e18, -1e18

        vbase = 0
        for bidx, b in enumerate(items):
            a = b["attrs"]
            bt["gml_id"].append(a["gml_id"])
            bt["name"].append(a["name"])
            bt["usage"].append(a["usage"])
            bt["height"].append(float(a["height"]))
            bt["storeys"].append(int(a["storeys"]))
            bt["lod"].append(int(a["lod"]))

            for ext, ints in b["polys"]:
                e = np.array(ext, dtype=np.float64)
                min_lat = min(min_lat, e[:, 0].min()); max_lat = max(max_lat, e[:, 0].max())
                min_lon = min(min_lon, e[:, 1].min()); max_lon = max(max_lon, e[:, 1].max())
                min_h = min(min_h, e[:, 2].min()); max_h = max(max_h, e[:, 2].max())

                ecef_ext = geodetic_to_ecef(e[:, 0], e[:, 1], e[:, 2])
                ecef_ints = [
                    geodetic_to_ecef(
                        np.array(h)[:, 0], np.array(h)[:, 1], np.array(h)[:, 2]
                    )
                    for h in ints
                    if len(h) >= 3
                ]
                verts, tris = triangulate(ecef_ext, ecef_ints)
                if verts is None or not tris:
                    continue

                nrm = _newell_normal(verts)
                if nrm is None:
                    continue
                c = float(np.dot(nrm, up))
                if c > ROOF_COS:
                    mat = MAT_INDEX["roof"]
                elif c < GROUND_COS:
                    mat = MAT_INDEX["ground"]
                else:
                    mat = MAT_INDEX["wall"]

                rel = verts - rtc
                # ECEF(Z-up) -> glTF(Y-up)
                gl = np.stack([rel[:, 0], rel[:, 2], -rel[:, 1]], axis=-1)
                gn = np.array([nrm[0], nrm[2], -nrm[1]], dtype=np.float64)

                positions.append(gl)
                normals.append(np.tile(gn, (len(gl), 1)))
                batchids.append(np.full(len(gl), bidx, dtype=np.float64))
                for t in tris:
                    prim_idx[mat].extend([t[0] + vbase, t[1] + vbase, t[2] + vbase])
                vbase += len(gl)

        if not positions:
            tile_no -= 1
            continue

        P = np.concatenate(positions).astype(np.float32)
        N = np.concatenate(normals).astype(np.float32)
        B = np.concatenate(batchids).astype(np.float32)
        prim = {k: np.array(v, dtype=np.uint32) for k, v in prim_idx.items()}
        total_tris += sum(len(v) for v in prim.values()) // 3

        glb = build_glb(P, N, B, prim)
        b3dm = build_b3dm(glb, rtc, bt)
        uri = f"tiles/tile_{ci}_{cj}.b3dm"
        with open(os.path.join(output_dir, uri), "wb") as fh:
            fh.write(b3dm)

        children.append(
            {
                "boundingVolume": {
                    "region": [
                        math.radians(min_lon),
                        math.radians(min_lat),
                        math.radians(max_lon),
                        math.radians(max_lat),
                        min_h - 5.0,
                        max_h + 5.0,
                    ]
                },
                "geometricError": 0.0,
                "refine": "ADD",
                "content": {"uri": uri},
            }
        )

    # ルート境界
    all_lon = [c["boundingVolume"]["region"] for c in children]
    root_region = [
        min(r[0] for r in all_lon),
        min(r[1] for r in all_lon),
        max(r[2] for r in all_lon),
        max(r[3] for r in all_lon),
        min(r[4] for r in all_lon),
        max(r[5] for r in all_lon),
    ]

    tileset = {
        "asset": {
            "version": "1.0",
            "tilesetVersion": "hibiya-digital-twin/1.0",
        },
        "properties": {},
        "geometricError": 500.0,
        "root": {
            "boundingVolume": {"region": root_region},
            "geometricError": 120.0,
            "refine": "ADD",
            "children": children,
        },
    }
    with open(os.path.join(output_dir, "tileset.json"), "w", encoding="utf-8") as fh:
        json.dump(tileset, fh, ensure_ascii=False, separators=(",", ":"))

    meta = {
        "buildings": len(buildings),
        "tiles": len(children),
        "triangles": total_tris,
        "center": [
            math.degrees((root_region[0] + root_region[2]) / 2),
            math.degrees((root_region[1] + root_region[3]) / 2),
        ],
    }
    with open(os.path.join(output_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    print(
        f"[✓] 出力完了: {output_dir}/tileset.json  "
        f"({len(children)} タイル / {len(buildings)} 棟 / {total_tris:,} 三角形)"
    )
    return 0


def main():
    ap = argparse.ArgumentParser(description="PLATEAU CityGML -> 3D Tiles (b3dm)")
    ap.add_argument("--input", default="input", help="CityGML(.gml) を含むディレクトリ")
    ap.add_argument("--output", default="data/hibiya", help="出力ディレクトリ")
    ap.add_argument(
        "--bbox",
        default="139.7480,35.6660,139.7700,35.6820",
        help="west,south,east,north（度）。'all' で範囲制限なし",
    )
    ap.add_argument("--cell", type=float, default=250.0, help="タイル1辺の概算メートル")
    ap.add_argument("--lod", type=int, default=0, choices=[0, 1], help="0=LOD2優先, 1=LOD1固定")
    args = ap.parse_args()

    bbox = None
    if args.bbox and args.bbox.lower() != "all":
        parts = [float(x) for x in args.bbox.split(",")]
        if len(parts) != 4:
            print("[!] --bbox は west,south,east,north 形式", file=sys.stderr)
            return 2
        bbox = parts

    os.makedirs(args.output, exist_ok=True)
    return convert(args.input, args.output, bbox, args.cell, args.lod)


if __name__ == "__main__":
    sys.exit(main())
