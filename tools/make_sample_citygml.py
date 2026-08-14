#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
変換パイプラインの動作確認用に、PLATEAU と同じ構造 (CityGML 2.0 / EPSG:6697,
lat lon height 順の posList) のダミー建築物 GML を生成する。
実データが届く前の検証専用。日比谷周辺にブロックを並べる。
"""
import math
import os
import random
import sys

HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel
  xmlns:core="http://www.opengis.net/citygml/2.0"
  xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
  xmlns:gen="http://www.opengis.net/citygml/generics/2.0"
  xmlns:gml="http://www.opengis.net/gml"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
"""
FOOTER = "</core:CityModel>\n"


def ring(coords):
    txt = " ".join(f"{lat:.9f} {lon:.9f} {h:.3f}" for lat, lon, h in coords)
    return (
        '<gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing>'
        f'<gml:posList srsDimension="3">{txt}</gml:posList>'
        "</gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>"
    )


def box(gid, clat, clon, w_m, d_m, h, base=2.0, usage="411", storeys=5, name=""):
    dlat = (d_m / 2) / 111320.0
    dlon = (w_m / 2) / (111320.0 * math.cos(math.radians(clat)))
    p = [
        (clat - dlat, clon - dlon),
        (clat - dlat, clon + dlon),
        (clat + dlat, clon + dlon),
        (clat + dlat, clon - dlon),
    ]
    top = base + h
    faces = []
    # 底面（下向き）
    faces.append(ring([(a, b, base) for a, b in reversed(p)] + [(p[-1][0], p[-1][1], base)][:0]))
    # 屋根（上向き）
    faces.append(ring([(a, b, top) for a, b in p]))
    # 側面
    for i in range(4):
        a = p[i]
        b = p[(i + 1) % 4]
        faces.append(
            ring([(a[0], a[1], base), (b[0], b[1], base), (b[0], b[1], top), (a[0], a[1], top)])
        )
    return f"""  <core:cityObjectMember>
    <bldg:Building gml:id="{gid}">
      <gml:name>{name}</gml:name>
      <bldg:usage codeSpace="../../codelists/Building_usage.xml">{usage}</bldg:usage>
      <bldg:measuredHeight uom="m">{h:.1f}</bldg:measuredHeight>
      <bldg:storeysAboveGround>{storeys}</bldg:storeysAboveGround>
      <bldg:lod1Solid>
        <gml:Solid><gml:exterior><gml:CompositeSurface>
          {''.join(faces)}
        </gml:CompositeSurface></gml:exterior></gml:Solid>
      </bldg:lod1Solid>
    </bldg:Building>
  </core:cityObjectMember>
"""


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "input/53394600_bldg_6697_2_op_SAMPLE.gml"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    random.seed(20260803)
    parts = [HEADER]
    # 日比谷交差点周辺のブロック配置
    lat0, lon0 = 35.6745, 139.7592
    n = 0
    for i in range(-6, 7):
        for j in range(-6, 7):
            clat = lat0 + i * 0.00075 + random.uniform(-0.00008, 0.00008)
            clon = lon0 + j * 0.00090 + random.uniform(-0.00010, 0.00010)
            h = random.choice([12, 18, 24, 31, 40, 55, 78, 92, 140])
            parts.append(
                box(
                    f"bldg_sample_{i}_{j}",
                    clat,
                    clon,
                    random.uniform(28, 62),
                    random.uniform(26, 55),
                    h,
                    usage=random.choice(["411", "412", "413", "421"]),
                    storeys=max(1, int(h / 4)),
                    name=f"サンプル棟 {i},{j}",
                )
            )
            n += 1
    parts.append(FOOTER)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("".join(parts))
    print(f"[✓] ダミー CityGML 生成: {out} ({n} 棟)")


if __name__ == "__main__":
    main()
