#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PLATEAU 東京都23区 CityGML の巨大 ZIP から、日比谷周辺に必要な
3次メッシュの建築物(bldg) GML だけを取り出す。

    python3 tools/extract_hibiya.py ~/Downloads/13100_tokyo23-ku_*_citygml_*.zip

既定の対象メッシュ（日比谷交差点を中心に半径 1〜1.5km 相当）:
    53394600  日比谷・内幸町・西幸門                （中心）
    53394601  有楽町・銀座西
    53394610  皇居外苑・丸の内
    53394611  丸の内・京橋
    53394509  霞が関・虎ノ門（西隣メッシュの東端）
    53394519  永田町・国会周辺
"""
import argparse
import os
import re
import sys
import zipfile

DEFAULT_MESHES = ["53394600", "53394601", "53394610", "53394611", "53394509", "53394519"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zip_path", help="PLATEAU CityGML の ZIP")
    ap.add_argument("--output", default="input", help="展開先")
    ap.add_argument("--mesh", nargs="*", default=DEFAULT_MESHES, help="対象3次メッシュコード")
    ap.add_argument("--feature", default="bldg", help="地物種別 (bldg, tran, bridge, ...)")
    ap.add_argument("--list", action="store_true", help="展開せず該当ファイルを一覧表示")
    args = ap.parse_args()

    if not os.path.exists(args.zip_path):
        print(f"[!] ZIP が見つかりません: {args.zip_path}", file=sys.stderr)
        return 1

    os.makedirs(args.output, exist_ok=True)
    pat = re.compile(r"(" + "|".join(map(re.escape, args.mesh)) + r").*_" + re.escape(args.feature) + r"_.*\.gml$", re.I)

    total = 0
    with zipfile.ZipFile(args.zip_path) as zf:
        names = zf.namelist()
        gmls = [n for n in names if n.lower().endswith(".gml")]
        hits = [n for n in gmls if pat.search(os.path.basename(n))]
        if not hits:
            # 種別フォルダ名で絞り込む形式にも対応
            hits = [
                n for n in gmls
                if f"/{args.feature}/" in n.replace("\\", "/")
                and any(m in os.path.basename(n) for m in args.mesh)
            ]
        if not hits:
            print(f"[!] 該当ファイルなし。ZIP 内の {args.feature} GML 例:", file=sys.stderr)
            for n in [g for g in gmls if f"/{args.feature}/" in g.replace('\\', '/')][:15]:
                print("    ", n, file=sys.stderr)
            return 1

        print(f"[i] 該当 {len(hits)} ファイル")
        for n in hits:
            info = zf.getinfo(n)
            print(f"    {os.path.basename(n)}  ({info.file_size/1048576:.1f} MB)")
            if args.list:
                continue
            dest = os.path.join(args.output, os.path.basename(n))
            with zf.open(n) as src, open(dest, "wb") as dst:
                while True:
                    buf = src.read(1 << 20)
                    if not buf:
                        break
                    dst.write(buf)
            total += info.file_size

        # codelists も一緒に取り出す（属性の名称解決に使う）
        if not args.list:
            for n in names:
                if "codelists/" in n.replace("\\", "/") and n.lower().endswith(".xml"):
                    dest = os.path.join(args.output, "codelists", os.path.basename(n))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with zf.open(n) as src, open(dest, "wb") as dst:
                        dst.write(src.read())

    if not args.list:
        print(f"[✓] {args.output}/ に展開しました（計 {total/1048576:.1f} MB）")
        print("    次: python3 tools/citygml2tiles.py --input input --output data/hibiya")
    return 0


if __name__ == "__main__":
    sys.exit(main())
