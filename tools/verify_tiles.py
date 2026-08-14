#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成した 3D Tiles を構造検証する（b3dm ヘッダ / glTF / 座標の逆変換）。"""
import glob
import json
import math
import os
import struct
import sys

import numpy as np

A = 6378137.0
F = 1.0 / 298.257223563
E2 = F * (2.0 - F)


def ecef_to_geodetic(x, y, z):
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - E2))
    for _ in range(8):
        n = A / math.sqrt(1 - E2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - E2 * n / (n + h)))
    n = A / math.sqrt(1 - E2 * math.sin(lat) ** 2)
    h = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), h


def check(path):
    raw = open(path, "rb").read()
    magic, ver, blen, ftj, ftb, btj, btb = struct.unpack("<4sIIIIII", raw[:28])
    assert magic == b"b3dm", f"{path}: bad magic {magic}"
    assert ver == 1, f"{path}: bad version"
    assert blen == len(raw), f"{path}: byteLength {blen} != {len(raw)}"
    o = 28
    ft = json.loads(raw[o : o + ftj]); o += ftj + ftb
    bt = json.loads(raw[o : o + btj]); o += btj + btb
    assert o % 8 == 0, f"{path}: glb not 8-byte aligned (offset {o})"
    glb = raw[o:]
    gmagic, gver, gtotal = struct.unpack("<III", glb[:12])
    assert gmagic == 0x46546C67, f"{path}: bad glb magic"
    assert gver == 2 and gtotal == len(glb), f"{path}: glb length mismatch"
    jlen, jtype = struct.unpack("<II", glb[12:20])
    assert jtype == 0x4E4F534A
    gltf = json.loads(glb[20 : 20 + jlen])
    blen2, btype = struct.unpack("<II", glb[20 + jlen : 28 + jlen])
    assert btype == 0x004E4942
    binc = glb[28 + jlen : 28 + jlen + blen2]
    assert gltf["buffers"][0]["byteLength"] <= len(binc), f"{path}: buffer overflow"

    # accessor / bufferView 範囲チェック
    for i, acc in enumerate(gltf["accessors"]):
        bv = gltf["bufferViews"][acc["bufferView"]]
        comp = {5125: 4, 5126: 4}[acc["componentType"]]
        ncomp = {"SCALAR": 1, "VEC3": 3}[acc["type"]]
        need = acc["count"] * comp * ncomp
        assert need <= bv["byteLength"], f"{path}: accessor {i} overruns bufferView"
        assert bv["byteOffset"] + bv["byteLength"] <= len(binc), f"{path}: bufferView OOB"

    # インデックス範囲
    pos_acc = gltf["accessors"][0]
    nverts = pos_acc["count"]
    for prim in gltf["meshes"][0]["primitives"]:
        acc = gltf["accessors"][prim["indices"]]
        bv = gltf["bufferViews"][acc["bufferView"]]
        idx = np.frombuffer(binc, dtype="<u4", count=acc["count"], offset=bv["byteOffset"])
        assert idx.max() < nverts, f"{path}: index out of range"
        assert acc["count"] % 3 == 0, f"{path}: index count not multiple of 3"
        assert prim["material"] < len(gltf["materials"])
        assert prim["attributes"]["_BATCHID"] == 2

    # BATCHID 範囲
    bacc = gltf["accessors"][2]
    bbv = gltf["bufferViews"][bacc["bufferView"]]
    bids = np.frombuffer(binc, dtype="<f4", count=bacc["count"], offset=bbv["byteOffset"])
    assert bids.max() < ft["BATCH_LENGTH"], f"{path}: BATCHID >= BATCH_LENGTH"
    assert len(bt["gml_id"]) == ft["BATCH_LENGTH"]

    # 位置を ECEF に戻して緯度経度を確認
    pbv = gltf["bufferViews"][pos_acc["bufferView"]]
    P = np.frombuffer(binc, dtype="<f4", count=nverts * 3, offset=pbv["byteOffset"]).reshape(-1, 3)
    rtc = np.array(ft["RTC_CENTER"])
    # glTF(Y-up) -> ECEF
    ecef = np.stack([P[:, 0], -P[:, 2], P[:, 1]], axis=-1) + rtc
    lats, lons, hs = [], [], []
    for v in ecef[:: max(1, len(ecef) // 40)]:
        la, lo, h = ecef_to_geodetic(*v)
        lats.append(la); lons.append(lo); hs.append(h)
    return {
        "file": os.path.basename(path),
        "batch": ft["BATCH_LENGTH"],
        "verts": nverts,
        "tris": sum(gltf["accessors"][p["indices"]]["count"] for p in gltf["meshes"][0]["primitives"]) // 3,
        "prims": len(gltf["meshes"][0]["primitives"]),
        "lat": (min(lats), max(lats)),
        "lon": (min(lons), max(lons)),
        "h": (min(hs), max(hs)),
    }


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "data/hibiya"
    ts = json.load(open(os.path.join(d, "tileset.json"), encoding="utf-8"))
    assert ts["asset"]["version"] == "1.0"
    files = sorted(glob.glob(os.path.join(d, "tiles", "*.b3dm")))
    assert len(files) == len(ts["root"]["children"]), "tileset の children とタイル数が不一致"
    rows = [check(f) for f in files]
    lat = (min(r["lat"][0] for r in rows), max(r["lat"][1] for r in rows))
    lon = (min(r["lon"][0] for r in rows), max(r["lon"][1] for r in rows))
    h = (min(r["h"][0] for r in rows), max(r["h"][1] for r in rows))
    print(f"[✓] b3dm {len(rows)} タイルすべて構造検証 OK")
    print(f"    building     : {sum(r['batch'] for r in rows)}")
    print(f"    triangles    : {sum(r['tris'] for r in rows):,}")
    print(f"    緯度範囲     : {lat[0]:.6f} - {lat[1]:.6f}")
    print(f"    経度範囲     : {lon[0]:.6f} - {lon[1]:.6f}")
    print(f"    標高範囲(m)  : {h[0]:.1f} - {h[1]:.1f}")
    reg = ts["root"]["boundingVolume"]["region"]
    print(
        "    root region  : "
        f"{math.degrees(reg[0]):.6f},{math.degrees(reg[1]):.6f} - "
        f"{math.degrees(reg[2]):.6f},{math.degrees(reg[3]):.6f}"
    )
    # 逆変換した実座標が region に収まっているか
    eps = 1e-6
    assert math.degrees(reg[0]) - eps <= lon[0] and lon[1] <= math.degrees(reg[2]) + eps, "経度が region 外"
    assert math.degrees(reg[1]) - eps <= lat[0] and lat[1] <= math.degrees(reg[3]) + eps, "緯度が region 外"
    print("[✓] 頂点座標は tileset の boundingVolume 内に収まっています")


if __name__ == "__main__":
    main()
