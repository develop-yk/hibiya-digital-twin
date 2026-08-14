#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""citygml2tiles.py が出力した b3dm のバッチテーブルを整える。

CityGML の属性はコード値のまま入っているため、そのままだと
ビューアに「401」「3002」といった数字が出てしまう。
配布ZIP 同梱のコードリストを引いて日本語ラベルに直し、
欠測値（PLATEAU は -9999 を使う）を取り除く。

    python3 tools/refine_tiles.py docs/tiles/hibiya/bldg_lod2

コードリストは配布ZIP から自動で探すが、ZIP を消してしまった場合は
PLATEAU の配信URLを渡せる（MCP の plateau_get_citygml_files で得られる URL の
末尾 /codelists/… を除いた部分）:

    python3 tools/refine_tiles.py --codelist-base https://assets.cms.plateau.reearth.io/assets/…/13101_chiyoda-ku_pref_2025_citygml_1_op docs/tiles/hibiya/bldg_lod2

ジオメトリ（glTF）には一切触らない。バッチテーブルのJSONだけ書き換える。
"""

from __future__ import annotations

import glob
import json
import os
import struct
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 欠測を表す値。PLATEAU では計測高さ等に -9999 が入る。
MISSING = (-9999, -9999.0)

CODELISTS = {
    "usage": "codelists/Building_usage.xml",
    "class": "codelists/Building_class.xml",
}


def _parse_codelist(data):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(data)
    out = {}
    loc = lambda t: t.rsplit("}", 1)[-1]           # noqa: E731
    for d in root.iter():
        if loc(d.tag) != "Definition":
            continue
        code = desc = None
        for c in d:
            if loc(c.tag) == "name":
                code = (c.text or "").strip()
            elif loc(c.tag) == "description":
                desc = (c.text or "").strip()
        if code and desc:
            out[code] = desc
    return out


def load_codelist(entry, base=""):
    """codelists/*.xml から {コード: 日本語} を作る。
    base（URL or ディレクトリ）が指定されていればそちらを優先し、
    無ければリポジトリ内の配布ZIP を探す。"""
    import xml.etree.ElementTree as ET
    if base:
        src = base.rstrip("/") + "/" + entry
        try:
            if src.startswith("http"):
                import urllib.request
                with urllib.request.urlopen(src, timeout=60) as fh:
                    return _parse_codelist(fh.read())
            with open(src, "rb") as fh:
                return _parse_codelist(fh.read())
        except Exception as exc:
            print("  ! %s を取得できません (%s)" % (src, exc))
    for zp in sorted(glob.glob(os.path.join(ROOT, "*", "*_citygml_*.zip"))):
        try:
            with zipfile.ZipFile(zp) as z:
                if entry not in z.namelist():
                    continue
                root = ET.fromstring(z.read(entry))
        except Exception:
            continue
        out = {}
        loc = lambda t: t.rsplit("}", 1)[-1]           # noqa: E731
        for d in root.iter():
            if loc(d.tag) != "Definition":
                continue
            code = desc = None
            for c in d:
                if loc(c.tag) == "name":
                    code = (c.text or "").strip()
                elif loc(c.tag) == "description":
                    desc = (c.text or "").strip()
            if code and desc:
                out[code] = desc
        if out:
            return out
    return {}


def refine_batch_table(bt, maps):
    n = len(bt.get("gml_id", []))
    out = {}
    for key, values in bt.items():
        if not isinstance(values, list):
            out[key] = values
            continue
        cl = maps.get(key)
        new = []
        for v in values:
            if v in MISSING or v == "" or v is None:
                new.append(None)
            elif cl:
                new.append(cl.get(str(v), v))
            elif key in ("height", "storeys") and isinstance(v, (int, float)) and v <= 0:
                new.append(None)
            else:
                new.append(v)
        out[key] = new
    # 全部 None の列は落とす（ビューアの属性表示が空行だらけになるのを防ぐ）
    out = {k: v for k, v in out.items()
           if not (isinstance(v, list) and len(v) == n and all(x is None for x in v))}
    return out


def rewrite_b3dm(path, maps):
    with open(path, "rb") as fh:
        b = fh.read()
    magic, ver, blen, ftjl, ftbl, btjl, btbl = struct.unpack("<4sIIIIII", b[:28])
    if magic != b"b3dm":
        return 0
    head = 28
    ft = b[head:head + ftjl + ftbl]
    bt_json = b[head + ftjl + ftbl: head + ftjl + ftbl + btjl]
    bt_bin = b[head + ftjl + ftbl + btjl: head + ftjl + ftbl + btjl + btbl]
    glb = b[head + ftjl + ftbl + btjl + btbl:]

    try:
        bt = json.loads(bt_json)
    except Exception:
        return 0
    new_bt = json.dumps(refine_batch_table(bt, maps),
                        separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    # glTF チャンクが 8 バイト境界から始まるように空白で詰める
    while (head + ftjl + ftbl + len(new_bt) + btbl) % 8 != 0:
        new_bt += b" "

    body = ft + new_bt + bt_bin + glb
    out = struct.pack("<4sIIIIII", b"b3dm", 1, 28 + len(body),
                      ftjl, ftbl, len(new_bt), btbl) + body
    with open(path, "wb") as fh:
        fh.write(out)
    return len(b) - len(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    base = ""
    rest = sys.argv[1:]
    for i, a in enumerate(rest):
        if a == "--codelist-base" and i + 1 < len(rest):
            base = rest[i + 1]
            if base in args:
                args.remove(base)
        elif a.startswith("--codelist-base="):
            base = a.split("=", 1)[1]
    if not args:
        print(__doc__)
        return 1
    maps = {}
    for key, entry in CODELISTS.items():
        cl = load_codelist(entry, base)
        if cl:
            maps[key] = cl
            print("  コードリスト %s: %d 件" % (key, len(cl)))
        else:
            print("  コードリスト %s: 見つかりません（コードのまま出力）" % key)

    total_saved = 0
    for d in args:
        files = sorted(glob.glob(os.path.join(d, "tiles", "*.b3dm")))
        if not files:
            print("  %s: b3dm がありません" % d)
            continue
        saved = sum(rewrite_b3dm(p, maps) for p in files)
        total_saved += saved
        print("  %s: %d タイルを整形（%+.0f KB）" % (d, len(files), -saved / 1024))
    print("合計 %+.1f MB" % (-total_saved / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
