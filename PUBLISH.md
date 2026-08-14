# GitHub Pages に公開する（ターミナル不要）

公開データを **170MB → 48.7MB** に作り直しました。あとはアップロードするだけです。

---

## なぜ今まで失敗していたか

エラーは毎回「The remote disconnected」でしたが、**回線の問題ではありません。**
未pushのコミットに **配布ZIP 6.4GB が入ったまま**で、毎回それを送ろうとしていました。

| 未pushコミットの中身 | サイズ |
|---|---|
| `chuo-ku/..._citygml_1_op.zip` | 2,360 MB |
| `chiyoda-ku/..._citygml_1_op.zip` | 2,010 MB |
| `chuo-ku/..._3dtiles_mvt_1_op.zip` | 807 MB |
| `chiyoda-ku/..._3dtiles_mvt_1_op.zip` | 803 MB |
| `_to_delete/` の作業ファイル | 481 MB |

GitHub は 1ファイル 100MB 超を受け付けません。
そして `.gitignore` は**すでに追跡されているファイルには効かない**ので、
今のリポジトリのままでは何度やっても通りません。

**そこで、リポジトリを新しくクローンし直します。**
まっさらなクローンには ZIP の履歴が無く、`.gitignore` も最初から効くので、
送信されるのは公開ファイルの 48.7MB だけになります。

---

## 手順（すべて GitHub Desktop と Finder）

### 1. 新しくクローンする

GitHub Desktop で **File → Clone repository → develop-yk/hibiya-digital-twin**

Local path を **今と違う場所**にします。例:

```
~/Documents/__Claude/hibiya-pages
```

> 今のフォルダ（`hibiya-digital-twin`）はそのまま残します。
> 配布ZIP と CityGML が入っていて、ローカルで全域を見るのに使うためです。

### 2. 公開ファイルをコピーする

Finder で `hibiya-digital-twin` を開き、次の **7項目だけ**を選んで、
クローンした `hibiya-pages` フォルダにコピーします。

- `docs`  ← 公開サイト本体（48.7MB）
- `.gitignore`  ← 見えない場合は `Command + Shift + .` で表示
- `README.md`
- `PUBLISH.md`
- `serve.py`
- `viewer`
- `tools`

**コピーしないもの**（容量が大きく、公開に不要）:
`chiyoda-ku` `chuo-ku` `input` `data` `_to_delete` `.git`

> 万一まとめてコピーしてしまっても、`.gitignore` が ZIP と CityGML を
> 除外するので、コミット対象には入りません（フォルダのコピーに時間がかかるだけです）。

### 3. コミットして Push

GitHub Desktop で `hibiya-pages` を選び、

1. 左下の Summary に `PLATEAU 3D VIEW サイト` などと入力
2. **Commit to main**
3. **Push origin**

送信量は 48.7MB です。

### 4. Pages を有効にする

GitHub のリポジトリページで **Settings → Pages → Build and deployment**

- Source: `Deploy from a branch`
- Branch: `main` / folder: **`/docs`**
- Save

1〜2分でここに公開されます:

**<https://develop-yk.github.io/hibiya-digital-twin/>**

---

## 公開データの中身

CityGML から自前変換した建築物のみです（災害リスク等は含みません）。

| レイヤー | 内容 | 容量 |
|---|---|---|
| 建築物 LOD2（屋根形状） | 2,101棟・47タイル。日比谷中心 1.6×1.4km | 32.0 MB |
| 建築物 LOD1（簡易形状・広域） | 4,614棟・52タイル。2.3×1.9km と広め | 6.1 MB |
| CesiumJS 同梱版 | CDN が遮断された環境でも動くように同梱 | 10.7 MB |

配布の 3D Tiles をそのまま使うと、**同じ範囲・同じ建物で LOD2 が 194MB**
（テクスチャ無しでも 72MB）になります。CityGML から起こすと
テクスチャと重複属性が付かないぶん **32MB** に収まります。

建物をクリックすると、名称・用途・計測高さ・地上階数・LOD・建物ID が出ます。
色分けは 単色／高さ／用途／階数 の4種です。

---

## 片付け

公開が終わったら、今のフォルダの残骸を消せます。

```bash
bash tools/cleanup.sh
```

| 対象 | サイズ |
|---|---|
| `_to_delete/`（作業ファイル） | 743 MB |
| `input/hibiya/`（展開した CityGML） | 551 MB |
| `.git` の到達不能オブジェクト（過去にZIPをコミットした残骸） | 約 6 GB |

配布ZIP（`chiyoda-ku/` `chuo-ku/` 計 5.9GB）は残します。
`serve.py` で千代田区・中央区の全域をテクスチャ付きで見るのに使います。

これだけはターミナルが必要ですが、**やらなくても公開には影響しません**。
ディスクを空けたくなったときで構いません。

---

## 収録範囲・LODを変えたいとき

```bash
# CityGML を配布ZIPから展開（初回のみ、約12秒）
python3 - <<'EOF'
import zipfile, os
z = zipfile.ZipFile("chiyoda-ku/13101_chiyoda-ku_pref_2025_citygml_1_op.zip")
os.makedirs("input/hibiya", exist_ok=True)
for m in ["53394600", "53394601", "53394610", "53394611"]:
    n = "udx/bldg/%s_bldg_6697_op.gml" % m
    open("input/hibiya/%s_bldg_6697_op.gml" % m, "wb").write(z.read(n))
EOF

# 変換（--bbox は west,south,east,north）
python3 tools/citygml2tiles.py --input input/hibiya \
    --output docs/tiles/hibiya/bldg_lod2 \
    --bbox 139.7520,35.6680,139.7700,35.6810 --cell 250
python3 tools/citygml2tiles.py --input input/hibiya \
    --output docs/tiles/hibiya/bldg_lod1 \
    --bbox 139.7500,35.6667,139.7750,35.6833 --cell 300 --lod 1

# 属性の整形（用途コード→日本語、欠測値 -9999 の除去）
python3 tools/refine_tiles.py docs/tiles/hibiya/bldg_lod2 docs/tiles/hibiya/bldg_lod1

# catalog.json を作り直す
python3 tools/build_pages_min.py

# 確認
python3 -m http.server -d docs 8000     # → http://localhost:8000/
```

範囲を広げるときは、必要な3次メッシュを `input/hibiya/` に追加してください
（メッシュコードは `53394600` が日比谷・内幸町、`53394601` が有楽町・銀座西、
`53394610` が皇居外苑・丸の内、`53394611` が丸の内・京橋）。

参考値（LOD2・自前変換）:

| 範囲 | 棟数 | 容量 |
|---|---|---|
| 1.6 × 1.4 km（現在） | 2,101 | 32 MB |
| 2.3 × 1.9 km（4メッシュ全域） | 4,615 | 60 MB |
