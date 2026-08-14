#!/usr/bin/env python3
"""3D Tiles(b3dm) -> Blender 向けの単一 glTF(.glb) にまとめる。

ビューアで使っている b3dm は
  ・glTF 2.0 が丸ごと入っている（Draco 等の圧縮なし）
  ・座標は「RTC_CENTER(ECEF) からの相対値」を Y-up に並べ替えたもの
なので、逆変換して ECEF に戻し、指定した原点まわりの ENU(東/北/上, m) に
置き換えれば、そのまま実寸の Z-up モデルになる。

出力 glTF は仕様どおり Y-up で書くので、Blender のインポータが
自動で Z-up に直してくれる（= Blender 上で東=+X, 北=+Y, 上=+Z）。
"""
import argparse
import glob
import json
import os
import struct
import sys

import numpy as np

WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)


def geodetic_to_ecef(lat_deg, lon_deg, h):
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    n = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(lat) ** 2)
    return np.array([(n + h) * np.cos(lat) * np.cos(lon),
                     (n + h) * np.cos(lat) * np.sin(lon),
                     (n * (1 - WGS84_E2) + h) * np.sin(lat)])


def enu_basis(lat_deg, lon_deg):
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    sla, cla = np.sin(lat), np.cos(lat)
    slo, clo = np.sin(lon), np.cos(lon)
    # 行が east / north / up
    return np.array([[-slo, clo, 0.0],
                     [-sla * clo, -sla * slo, cla],
                     [cla * clo, cla * slo, sla]])


def read_b3dm(path):
    d = open(path, 'rb').read()
    if d[:4] != b'b3dm':
        raise ValueError('not b3dm: ' + path)
    _, _, _, ftj, ftb, btj, btb = struct.unpack('<4sIIIIII', d[:28])
    o = 28
    ft = json.loads(d[o:o + ftj].decode('utf-8'))
    o += ftj + ftb + btj + btb
    gl = d[o:]
    cl, _ = struct.unpack('<II', gl[12:20])
    j = json.loads(gl[20:20 + cl].decode('utf-8'))
    bl, _ = struct.unpack('<II', gl[20 + cl:28 + cl])
    bin_ = gl[28 + cl:28 + cl + bl]
    return ft, j, bin_


CTYPE = {5120: 'i1', 5121: 'u1', 5122: 'i2', 5123: 'u2', 5125: 'u4', 5126: 'f4'}
NCOMP = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}


def accessor(j, bin_, i):
    a = j['accessors'][i]
    bv = j['bufferViews'][a['bufferView']]
    off = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
    n = NCOMP[a['type']]
    arr = np.frombuffer(bin_, dtype=CTYPE[a['componentType']],
                        count=a['count'] * n, offset=off)
    return arr.reshape(a['count'], n) if n > 1 else arr


def weld(P, N, I):
    """同じ位置・同じ法線の頂点をまとめる。1mm 単位で丸めて判定する。"""
    key = np.concatenate([np.round(P * 1000.0).astype(np.int64),
                          np.round(N * 1000.0).astype(np.int64)], axis=1)
    view = np.ascontiguousarray(key).view([('', np.int64)] * 6).ravel()
    _, first, inv = np.unique(view, return_index=True, return_inverse=True)
    return P[first], N[first], inv[I].astype(np.uint32)


def collect(layer_dir, origin_ecef, R):
    """レイヤー配下の b3dm を全部読んで material 名ごとにまとめる。"""
    files = sorted(glob.glob(os.path.join(layer_dir, 'tiles', '*.b3dm')))
    buckets = {}   # matname -> {"P":[], "N":[], "I":[], "base":int, "color":[..]}
    for k, f in enumerate(files):
        ft, j, bin_ = read_b3dm(f)
        rtc = np.array(ft.get('RTC_CENTER', [0, 0, 0]), dtype=np.float64)
        mats = [m['name'] for m in j.get('materials', [])]
        colors = [m.get('pbrMetallicRoughness', {}).get('baseColorFactor',
                  [0.8, 0.8, 0.8, 1.0]) for m in j.get('materials', [])]
        for prim in j['meshes'][0]['primitives']:
            mi = prim.get('material', 0)
            name = mats[mi] if mi < len(mats) else 'mat%d' % mi
            idx = accessor(j, bin_, prim['indices']).astype(np.int64)
            pos = accessor(j, bin_, prim['attributes']['POSITION']).astype(np.float64)
            nrm = accessor(j, bin_, prim['attributes']['NORMAL']).astype(np.float64)

            used, inv = np.unique(idx, return_inverse=True)
            pos = pos[used]
            nrm = nrm[used]

            # glTF(Y-up, RTC相対) -> ECEF相対 -> ECEF -> ENU
            ecef_rel = np.stack([pos[:, 0], -pos[:, 2], pos[:, 1]], axis=-1)
            d = ecef_rel + rtc - origin_ecef
            enu = d @ R.T
            nrm_ecef = np.stack([nrm[:, 0], -nrm[:, 2], nrm[:, 1]], axis=-1)
            nrm_enu = nrm_ecef @ R.T

            b = buckets.setdefault(name, {'P': [], 'N': [], 'I': [], 'base': 0,
                                          'color': colors[mi] if mi < len(colors)
                                          else [0.8, 0.8, 0.8, 1.0]})
            b['I'].append(inv.astype(np.int64) + b['base'])
            b['base'] += len(enu)
            b['P'].append(enu)
            b['N'].append(nrm_enu)
        if (k + 1) % 50 == 0:
            print('    %d/%d' % (k + 1, len(files)), flush=True)

    out = {}
    for name, b in buckets.items():
        P = np.concatenate(b['P'])
        N = np.concatenate(b['N'])
        I = np.concatenate(b['I'])
        P, N, I = weld(P.astype(np.float32), N.astype(np.float32), I)
        out[name] = (P, N, I, b['color'])
    return out


def build_glb(parts, path):
    """parts: [(nodename, P(ENU,Z-up), N, I, color)] -> .glb (glTF は Y-up)"""
    buf = bytearray()
    views, accs, meshes, nodes, materials = [], [], [], [], []

    def add_view(data, target):
        while len(buf) % 4:
            buf.append(0)
        off = len(buf)
        buf.extend(data)
        views.append({'buffer': 0, 'byteOffset': off,
                      'byteLength': len(data), 'target': target})
        return len(views) - 1

    for name, P, N, I, color in parts:
        # ENU(Z-up) -> glTF(Y-up)
        g = np.stack([P[:, 0], P[:, 2], -P[:, 1]], axis=-1).astype(np.float32)
        gn = np.stack([N[:, 0], N[:, 2], -N[:, 1]], axis=-1).astype(np.float32)
        vp = add_view(g.tobytes(), 34962)
        vn = add_view(gn.tobytes(), 34962)
        vi = add_view(I.astype(np.uint32).tobytes(), 34963)
        accs.append({'bufferView': vp, 'componentType': 5126, 'count': len(g),
                     'type': 'VEC3', 'min': g.min(axis=0).tolist(),
                     'max': g.max(axis=0).tolist()})
        accs.append({'bufferView': vn, 'componentType': 5126, 'count': len(gn),
                     'type': 'VEC3'})
        accs.append({'bufferView': vi, 'componentType': 5125, 'count': len(I),
                     'type': 'SCALAR'})
        materials.append({'name': name, 'doubleSided': True,
                          'pbrMetallicRoughness': {
                              'baseColorFactor': list(color),
                              'metallicFactor': 0.0, 'roughnessFactor': 0.9}})
        meshes.append({'name': name, 'primitives': [{
            'attributes': {'POSITION': len(accs) - 3, 'NORMAL': len(accs) - 2},
            'indices': len(accs) - 1, 'material': len(materials) - 1, 'mode': 4}]})
        nodes.append({'name': name, 'mesh': len(meshes) - 1})

    j = {'asset': {'version': '2.0', 'generator': 'hibiya-digital-twin tiles2mesh'},
         'scene': 0, 'scenes': [{'nodes': list(range(len(nodes)))}],
         'nodes': nodes, 'meshes': meshes, 'materials': materials,
         'accessors': accs, 'bufferViews': views,
         'buffers': [{'byteLength': len(buf)}]}
    jb = json.dumps(j, separators=(',', ':')).encode('utf-8')
    jb += b' ' * ((4 - len(jb) % 4) % 4)
    bb = bytes(buf) + b'\x00' * ((4 - len(buf) % 4) % 4)
    total = 12 + 8 + len(jb) + 8 + len(bb)
    with open(path, 'wb') as f:
        f.write(struct.pack('<4sII', b'glTF', 2, total))
        f.write(struct.pack('<II', len(jb), 0x4E4F534A))
        f.write(jb)
        f.write(struct.pack('<II', len(bb), 0x004E4942))
        f.write(bb)
    return total


"""Blender 上で分かりやすい名前に置き換える。FBX/OBJ を他ソフトへ渡す
ことも考えて ASCII に留める。"""
LAYER_NAME = {'bldg_lod2': 'Building_LOD2', 'bldg_lod1': 'Building_LOD1',
              'tran': 'Road', 'zone': 'Zoning',
              'veg_tree': 'Tree', 'veg_cover': 'Greenery'}
MAT_NAME = {'wall': 'Wall', 'roof': 'Roof', 'ground': 'Ground'}


def obj_name(layer, mat):
    ln = LAYER_NAME.get(layer, layer)
    # 建物以外は wall/roof/ground の区別に意味がないので層名だけにする
    if not layer.startswith('bldg'):
        return ln
    return '%s_%s' % (ln, MAT_NAME.get(mat, mat))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tiles', default='tiles/hibiya')
    ap.add_argument('--layers', default='bldg_lod2,tran,veg_tree,veg_cover,zone')
    ap.add_argument('--lat', type=float, default=35.671526)
    ap.add_argument('--lon', type=float, default=139.756900)
    ap.add_argument('--out', default='hibiya.glb')
    a = ap.parse_args()

    origin = geodetic_to_ecef(a.lat, a.lon, 0.0)
    R = enu_basis(a.lat, a.lon)

    parts = []
    for lay in a.layers.split(','):
        d = os.path.join(a.tiles, lay)
        if not os.path.isdir(d):
            print('skip (なし):', lay)
            continue
        print('読み込み:', lay, flush=True)
        for mat, (P, N, I, c) in collect(d, origin, R).items():
            if len(I) == 0:
                continue
            nm = obj_name(lay, mat)
            parts.append((nm, P, N, I, c))
            print('   %-22s 頂点 %9d / 三角 %9d' % (nm, len(P), len(I) // 3))

    n = build_glb(parts, a.out)
    print('\n出力: %s  %.1f MB  オブジェクト %d' % (a.out, n / 1048576, len(parts)))
    print('原点: %.6f, %.6f (標高0m) / 単位: m / Blender上で 東=+X 北=+Y 上=+Z'
          % (a.lat, a.lon))


if __name__ == '__main__':
    sys.exit(main())
