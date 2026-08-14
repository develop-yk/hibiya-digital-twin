#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成した 3D Tiles(b3dm) をブラウザなしでソフトウェアレンダリングし、
ジオメトリ・法線・向きが正しいかを PNG で確認するための検証ツール。
（Cesium で表示する前のオフライン確認用）

    python3 tools/render_preview.py data/hibiya preview.png --heading 20 --pitch -35 --range 1400
"""
import argparse
import glob
import json
import math
import os
import struct
import sys
import zlib

import numpy as np

A = 6378137.0
F = 1.0 / 298.257223563
E2 = F * (2.0 - F)


def geodetic_to_ecef(lat, lon, h):
    la, lo = math.radians(lat), math.radians(lon)
    n = A / math.sqrt(1 - E2 * math.sin(la) ** 2)
    return np.array([
        (n + h) * math.cos(la) * math.cos(lo),
        (n + h) * math.cos(la) * math.sin(lo),
        (n * (1 - E2) + h) * math.sin(la),
    ])


def enu_basis(lat, lon):
    la, lo = math.radians(lat), math.radians(lon)
    e = np.array([-math.sin(lo), math.cos(lo), 0.0])
    n = np.array([-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la)])
    u = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
    return e, n, u


def read_b3dm(path):
    raw = open(path, "rb").read()
    _m, _v, _bl, ftj, ftb, btj, btb = struct.unpack("<4sIIIIII", raw[:28])
    o = 28
    ft = json.loads(raw[o:o + ftj]); o += ftj + ftb + btj + btb
    glb = raw[o:]
    jlen = struct.unpack("<I", glb[12:16])[0]
    gltf = json.loads(glb[20:20 + jlen])
    blen = struct.unpack("<I", glb[20 + jlen:24 + jlen])[0]
    binc = glb[28 + jlen:28 + jlen + blen]

    def acc(i, dtype, ncomp):
        a = gltf["accessors"][i]
        bv = gltf["bufferViews"][a["bufferView"]]
        arr = np.frombuffer(binc, dtype=dtype, count=a["count"] * ncomp, offset=bv["byteOffset"])
        return arr.reshape(-1, ncomp) if ncomp > 1 else arr

    P = acc(0, "<f4", 3).astype(np.float64)
    rtc = np.array(ft["RTC_CENTER"])
    ecef = np.stack([P[:, 0], -P[:, 2], P[:, 1]], axis=-1) + rtc
    tris, mats = [], []
    for prim in gltf["meshes"][0]["primitives"]:
        idx = acc(prim["indices"], "<u4", 1).reshape(-1, 3)
        tris.append(idx)
        mats.append(np.full(len(idx), prim["material"], dtype=np.int32))
    return ecef, np.concatenate(tris), np.concatenate(mats)


def write_png(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    open(path, "wb").write(png)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tiles_dir")
    ap.add_argument("out_png")
    ap.add_argument("--width", type=int, default=1400)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--heading", type=float, default=20.0)
    ap.add_argument("--pitch", type=float, default=-35.0)
    ap.add_argument("--range", type=float, default=1500.0)
    args = ap.parse_args()

    ts = json.load(open(os.path.join(args.tiles_dir, "tileset.json"), encoding="utf-8"))
    reg = ts["root"]["boundingVolume"]["region"]
    clon = math.degrees((reg[0] + reg[2]) / 2)
    clat = math.degrees((reg[1] + reg[3]) / 2)
    origin = geodetic_to_ecef(clat, clon, 0.0)
    e, n, u = enu_basis(clat, clon)
    M = np.stack([e, n, u])  # ECEF -> ENU

    files = sorted(glob.glob(os.path.join(args.tiles_dir, "tiles", "*.b3dm")))
    if not files:
        print("b3dm がありません", file=sys.stderr)
        return 1

    V, T, Mt = [], [], []
    base = 0
    for f in files:
        ecef, tris, mats = read_b3dm(f)
        enu = (ecef - origin) @ M.T
        V.append(enu); T.append(tris + base); Mt.append(mats)
        base += len(enu)
    V = np.concatenate(V); T = np.concatenate(T); Mt = np.concatenate(Mt)

    # カメラ（ENU 座標系）
    hd, pt = math.radians(args.heading), math.radians(args.pitch)
    fwd = np.array([math.sin(hd) * math.cos(pt), math.cos(hd) * math.cos(pt), math.sin(pt)])
    eye = -fwd * args.range + np.array([0, 0, 60.0])
    zc = -fwd
    xc = np.cross(np.array([0, 0, 1.0]), zc); xc /= np.linalg.norm(xc)
    yc = np.cross(zc, xc)
    R = np.stack([xc, yc, zc])
    cam = (V - eye) @ R.T

    W, H = args.width, args.height
    fov = math.radians(45)
    fpx = (H / 2) / math.tan(fov / 2)
    z = -cam[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        sx = W / 2 + cam[:, 0] / z * fpx
        sy = H / 2 - cam[:, 1] / z * fpx

    # 空のグラデーション背景
    img = np.zeros((H, W, 3), dtype=np.float64)
    g = np.linspace(0, 1, H)[:, None]
    img[:, :, 0] = (0.055 + 0.10 * g)
    img[:, :, 1] = (0.075 + 0.13 * g)
    img[:, :, 2] = (0.105 + 0.17 * g)
    zbuf = np.full((H, W), np.inf)

    colors = np.array([[0.855, 0.843, 0.816], [0.596, 0.647, 0.702], [0.40, 0.40, 0.41]])
    light = np.array([0.42, -0.55, 0.72]); light /= np.linalg.norm(light)

    order = np.argsort(-z[T].mean(axis=1))
    drawn = 0
    for ti in order:
        tri = T[ti]
        if np.any(z[tri] <= 1.0):
            continue
        xs, ys = sx[tri], sy[tri]
        x0, x1 = int(max(0, np.floor(xs.min()))), int(min(W - 1, np.ceil(xs.max())))
        y0, y1 = int(max(0, np.floor(ys.min()))), int(min(H - 1, np.ceil(ys.max())))
        if x1 < x0 or y1 < y0:
            continue
        p = V[tri]
        nrm = np.cross(p[1] - p[0], p[2] - p[0])
        ln = np.linalg.norm(nrm)
        if ln < 1e-9:
            continue
        nrm /= ln
        lam = abs(float(np.dot(nrm, light)))
        col = colors[Mt[ti]] * (0.30 + 0.70 * lam)

        ax, ay = xs[0], ys[0]; bx, by = xs[1], ys[1]; cx, cy = xs[2], ys[2]
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-9:
            continue
        yy, xx = np.mgrid[y0:y1 + 1, x0:x1 + 1]
        px, py = xx + 0.5, yy + 0.5
        l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
        l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
        l3 = 1 - l1 - l2
        m = (l1 >= 0) & (l2 >= 0) & (l3 >= 0)
        if not m.any():
            continue
        zz = l1 * z[tri[0]] + l2 * z[tri[1]] + l3 * z[tri[2]]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        upd = m & (zz < sub)
        if not upd.any():
            continue
        sub[upd] = zz[upd]
        # 距離フォグ
        fog = np.clip((zz[upd] - 400) / 3000.0, 0, 0.55)[:, None]
        tgt = img[y0:y1 + 1, x0:x1 + 1][upd]
        img[y0:y1 + 1, x0:x1 + 1][upd] = col * (1 - fog) + np.array([0.16, 0.20, 0.26]) * fog
        drawn += 1

    out = np.clip(img ** (1 / 2.2), 0, 1)
    write_png(args.out_png, (out * 255).astype(np.uint8))
    print(f"[✓] プレビュー出力: {args.out_png}  (三角形 {drawn:,}/{len(T):,} 描画)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
