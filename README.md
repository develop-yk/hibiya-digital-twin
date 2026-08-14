# HIBIYA DIGITAL TWIN

国土交通省 **Project PLATEAU**（3D都市モデル）のオープンデータを、
**ZIPを展開せずそのまま** ブラウザで閲覧できる 3D VIEW サイト。

東京都 **千代田区（13101）／中央区（13102）** の 2025年度版 3D Tiles を対象に、
日比谷・丸の内・皇居外苑・銀座・日本橋・霞が関までを連続して閲覧できる。

**公開版（GitHub Pages）** … <https://develop-yk.github.io/hibiya-digital-twin/>
日比谷・有楽町周辺を CityGML から変換した軽量版（48.7MB）。建築物のみ。サーバ不要。

```
hibiya-digital-twin/
├── serve.py                ← 配信サーバ（ZIP内の3D Tilesを直接HTTP配信）
├── viewer/index.html       ← 3D VIEW 本体（単一HTML／CesiumJS）
├── docs/                   ← GitHub Pages で公開する静的版
│   ├── index.html          　 viewer/index.html と同一ファイル
│   ├── catalog.json        　 静的版のレイヤー定義
│   ├── tiles/              　 切り出した 3D Tiles
│   └── vendor/cesium/      　 CesiumJS 同梱（CDN 遮断環境でも動くように）
├── chiyoda-ku/*.zip        ← PLATEAU 配布ZIP（そのまま置くだけ。git 管理外）
├── chuo-ku/*.zip
├── tools/
│   ├── citygml2tiles.py    ← CityGML → 3D Tiles（公開版はこれで作る）
│   ├── refine_tiles.py     ← 用途コードの日本語化・欠測値の除去
│   ├── build_pages_min.py  ← 自前変換版の catalog.json を作る
│   ├── build_pages.py      ← 配布3D Tiles から docs/ を書き出す（別方式）
│   └── …                   ← CityGML → 3D Tiles 自前変換ツール一式（任意）
├── data/hibiya/            ← 自前変換の出力（任意）
└── input/                  ← CityGML(.gml) 置き場（任意）
```

配布ZIP は 1ファイル 0.8〜2.5GB あり GitHub の 100MB 制限を超えるため、
`.gitignore` で追跡対象から外している。
[G空間情報センター](https://www.geospatial.jp/ckan/organization/mlit-plateau) から
各自ダウンロードして `chiyoda-ku/` `chuo-ku/` に置くこと。

---

## 使い方

```bash
cd hibiya-digital-twin
python3 serve.py            # → http://localhost:8080/viewer/
```

依存パッケージなし（Python 3.8+ の標準ライブラリのみ）。
起動時に配布ZIPを走査し、見つかった 3D Tiles を自動でレイヤーとして登録する。

```
PLATEAU 配布ZIP を走査しています…
  ✓ 千代田区（13101） 20 レイヤ  ← chiyoda-ku/13101_..._3dtiles_mvt_1_op.zip
  ✓ 中央区（13102）   21 レイヤ  ← chuo-ku/13102_..._3dtiles_mvt_1_op.zip
  → 2 市区 / 26 レイヤ / 4,900 タイル / 3.1 GB  (0.6 秒)
```

> 3D Tiles は `file://` では読み込めない。必ずこのサーバ経由で開くこと。

### 別の市区を追加する

[G空間情報センター](https://www.geospatial.jp/ckan/organization/mlit-plateau) から
**「3D Tiles, MVT」** 形式のZIP（`<コード>_<市区>_..._3dtiles_mvt_1_op.zip`）を落として、
リポジトリ直下の任意のフォルダに置き、`serve.py` を再起動するだけでよい。
東京23区はコードから区名を自動判定する。

---

## なぜ展開しないのか

PLATEAU の配布ZIP は `.b3dm` を **無圧縮（STORED）** で格納している。
ZIPのセントラルディレクトリを引けば任意のタイルへ直接シークできるため、
展開してディスクを 2 倍消費する意味がない。

- 千代田区 ZIP … 841 MB（展開すると 4.5 GB）
- 中央区 ZIP … 846 MB（展開すると 3.2 GB）

`serve.py` はスレッドごとに `ZipFile` ハンドルを保持し、
リクエストされたタイルだけを読んで返す。ETag による 304 応答にも対応。

---

## GitHub Pages で公開する

公開版は **配布の 3D Tiles ではなく、CityGML から自前変換した建築物**を使う。
同じ範囲・同じ建物でも容量が桁違いに小さくなるため。

| 建築物データ（日比谷中心 1.6×1.4km・2,101棟） | 容量 |
|---|---|
| 配布 3D Tiles LOD2（テクスチャ付き） | 194 MB |
| 配布 3D Tiles LOD2（テクスチャ無し） | 72 MB |
| **CityGML から自前変換した LOD2** | **32 MB** |
| CityGML から自前変換した LOD1 | 3 MB |

配布データはテクスチャに加え、`attributes` という他の列と重複する巨大JSONを
1棟ごとに抱えている（無地版でも全体の21%）。自前変換ではこれらが付かない。

```bash
# 1. CityGML を配布ZIPから展開（初回のみ）
python3 tools/extract_hibiya.py chiyoda-ku/13101_..._citygml_1_op.zip

# 2. 変換（--bbox は west,south,east,north）
python3 tools/citygml2tiles.py --input input/hibiya \
    --output docs/tiles/hibiya/bldg_lod2 \
    --bbox 139.7520,35.6680,139.7700,35.6810 --cell 250
python3 tools/citygml2tiles.py --input input/hibiya \
    --output docs/tiles/hibiya/bldg_lod1 \
    --bbox 139.7500,35.6667,139.7750,35.6833 --cell 300 --lod 1

# 3. 属性を整形（用途コード→日本語、欠測値 -9999 の除去）
python3 tools/refine_tiles.py docs/tiles/hibiya/bldg_lod2 docs/tiles/hibiya/bldg_lod1

# 4. catalog.json を作る
python3 tools/build_pages_min.py

# 5. 確認
python3 -m http.server -d docs 8000     # → http://localhost:8000/
```

現在の `docs/` は **48.7MB / 322ファイル**（タイル 38.1MB ＋ CesiumJS 10.7MB）。

**公開の設定** … リポジトリの Settings → Pages → Source を
`Deploy from a branch` ／ Branch `main` ／ folder `/docs` にする。
`docs/.nojekyll` を置いてあるので Jekyll の処理は走らない。

アップロード手順は `PUBLISH.md` を参照（GitHub Desktop だけで完結する）。

### 配布 3D Tiles をそのまま公開したい場合

`tools/build_pages.py` が配布ZIPから範囲を切り出して `docs/` に並べる。
テクスチャ付きにしたい、災害リスクも載せたい、という場合はこちら。

```bash
python3 tools/build_pages.py --list     # 対象レイヤーと容量を確認
python3 tools/build_pages.py            # docs/ を書き出す
```

GitHub Pages の目安はサイト全体で 1 GB。範囲を広げると
建築物が一気に膨らむ（千代田＋中央の全域でテクスチャ無し 423MB）ので
`--list` で確認してから書き出すこと。

---

## 収録レイヤー

千代田区・中央区のZIPに含まれ、ビューアから切り替えられるもの。

| カテゴリ | レイヤー | 内容 |
|---|---|---|
| 建築物 | LOD2（テクスチャ） | 実写テクスチャ付きの屋根形状。既定でON |
| | LOD2（無地） | 同じ形状のテクスチャなし版（軽量） |
| | LOD1（簡易形状） | 箱型モデル。最軽量 |
| 都市インフラ | 道路 LOD3 / 橋梁 LOD2 | 車道・歩道の面、橋梁形状 |
| | 都市設備 LOD3 | 信号・標識・街灯・防護柵など |
| | 地下街・地下埋設物 LOD4 | 地下空間 |
| 自然 | 樹木（単木）LOD3 / 植被 LOD3 | 街路樹・公園の緑 |
| | 水部 LOD1 | 皇居の濠・日本橋川・隅田川など |
| 災害リスク | 洪水浸水想定 | 荒川水系神田川・神田川流域・隅田川／新河岸川流域・江東内部河川（L1/L2） |
| | 高潮浸水想定 | 東京都高潮浸水想定区域図（令和6年12月19日） |

土地利用・用途地域などの **MVT形式のレイヤーは対象外**（3D Tiles のみ表示する）。

---

## ビューアの機能

| 機能 | 内容 |
|---|---|
| **レイヤー切替** | カタログから自動生成。市区をまたいで1トグルで表示 |
| **建物の色分け** | テクスチャ／単色／高さ／用途／階数／用途地域／**浸水深** |
| **属性表示** | 建物をクリックすると名称・用途・高さ・階数・住所・建蔽率・容積率・建物ID・想定浸水深などを表示 |
| **日照・影** | 日付と時刻（JST）を指定して太陽位置に連動した影を描画 |
| **地形** | 地理院 標高タイル(`dem_png`)から生成する TerrainProvider。高さ強調 1〜8× |
| **背景地図** | 地理院 シームレス空中写真／淡色地図／標準地図／なし |
| **視点** | 日比谷交差点・日比谷公園・東京ミッドタウン日比谷・帝国ホテル・皇居外苑・東京駅・銀座四丁目・日本橋・霞が関・ストリートレベルなど |
| **描画品質** | タイル解像度を高精細〜軽量で調整。描画タイル数とGPUメモリを表示 |

- **Cesium ion のトークンは不要**（地理院タイルのみで完結）。
- CesiumJS は **リポジトリ同梱版（`docs/vendor/cesium/`）を最優先** で読み込み、
  取得できないときだけ jsDelivr → unpkg → cesium.com の順に CDN を試す。
  社内ネットワークで CDN が遮断されていても表示できる。
  別の場所に置いた Cesium を使いたい場合は `?cesiumBase=…/Build/Cesium/` を付けて開く。

> 画面が真っ黒でエラー帯が出る場合、まず CesiumJS の読み込みに失敗していないか
> ブラウザのコンソールを確認する。同梱版を使うようになったので、
> `docs/vendor/cesium/Cesium.js` が存在するかを見ればよい。

### 高さの基準について

PLATEAU 配布の 3D Tiles は高さが **楕円体高** で作られている
（CityGML の標高 T.P. にジオイド高を足した値）。
一方で地理院標高タイルは **標高（T.P.）** なので、そのまま重ねると
建物が約 36.6 m 浮く。

本ビューアは地形側にジオイド高を加算して両者を揃えている。
既定値は東京付近の **36.6 m**。左パネルの「ジオイド補正」で微調整できる。

### 3D Tiles スタイル式の落とし穴

建物の色分けは Cesium の 3D Tiles Styling で実装している。実機検証で判明した制約：

- `defined()` は CesiumJS の式パーサに **存在しない**（構文エラーになる）
- 属性を持たない地物に対して `${x} >= 10` を評価すると
  `RuntimeError` が投げられ **描画そのものが停止する**
- `Number(${x})` なら欠損時に `NaN` となり、比較は安全に `false` になる
- `max(NaN, 2.0)` は `NaN`。複数属性の最大値を `max()` で束ねてはいけない

そのため数値比較は必ず `Number()` でくるみ、「複数属性の最大値」は
しきい値の降順 × 属性ごとの条件を並べることで表現している
（`viewer/index.html` の `buildingStyle()` を参照）。

---

## 自前で CityGML から変換する（任意）

配布の 3D Tiles ではなく CityGML から自分で変換したい場合は `tools/` を使う。

```bash
python3 tools/extract_hibiya.py ~/Downloads/13100_tokyo23-ku_..._citygml_1_op.zip
python3 tools/citygml2tiles.py --input input --output data/hibiya \
        --bbox 139.7480,35.6660,139.7700,35.6820 --cell 250
python3 tools/verify_tiles.py data/hibiya
python3 tools/render_preview.py data/hibiya preview.png
```

| オプション | 意味 |
|---|---|
| `--bbox west,south,east,north` | 切り出す範囲（度）。`all` で全域 |
| `--cell` | タイル1辺の概算メートル（既定 250） |
| `--lod 1` | LOD2があってもLOD1に固定（軽量化） |

出力は `data/hibiya/tileset.json` に置かれ、`http://localhost:8080/data/hibiya/tileset.json`
として配信される（ビューアの既定レイヤーには含めていない）。

### テスト

```bash
python3 tools/test_triangulation.py    # 凸/凹/穴あき/逆巻き の三角形分割
python3 tools/test_citygml_parse.py    # LOD2優先・boundedBy・穴・属性抽出
python3 tools/verify_tiles.py data/hibiya
```

---

## 出典・ライセンス

- 3D都市モデル：**国土交通省 Project PLATEAU**（<https://www.mlit.go.jp/plateau/>）
  データは CC BY 4.0 等で提供されている。利用時は出典表示が必要。
- 背景地図・標高：**国土地理院タイル**（<https://maps.gsi.go.jp/development/ichiran.html>）
- 描画エンジン：**CesiumJS**（Apache-2.0）
