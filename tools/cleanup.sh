#!/bin/bash
# =============================================================================
# 作業で出た不要ファイルと、git の残骸を片付ける
#
#   bash tools/cleanup.sh          # 何がどれだけ消えるか表示して確認を求める
#   bash tools/cleanup.sh --yes    # 確認なしで実行
#
# 消すもの:
#   _to_delete/            … 検証用に作った一時ファイル（約 743MB）
#   __pycache__/           … Python のキャッシュ
#   .DS_Store              … Finder が作るもの
#   .git の到達不能オブジェクト … 過去に ZIP をコミットした残骸（約 6GB）
#
# 消さないもの:
#   chiyoda-ku/ chuo-ku/   … PLATEAU の配布ZIP（serve.py が使う元データ）
#   docs/ viewer/ tools/ data/ input/ とスクリプト類
# =============================================================================
set -u

cd "$(dirname "$0")/.." || exit 1
echo "リポジトリ: $(pwd)"
echo

YES=0
for a in "$@"; do
    case "$a" in
        --yes|-y) YES=1 ;;
        *) echo "不明なオプション: $a"; exit 1 ;;
    esac
done

# --- 安全確認：ここが本当に対象のリポジトリか ------------------------------
if [ ! -f serve.py ] || [ ! -d docs ]; then
    echo "✗ hibiya-digital-twin のディレクトリではないようです。中断します。"
    exit 1
fi

# --- 順序の確認：ZIPがまだコミットに残っていると .git は縮まない -------------
# 到達可能な（＝どこかのコミットから参照されている）巨大ファイルがあるかを見る。
HUGE=$(git rev-list --objects --all 2>/dev/null \
       | git cat-file --batch-check='%(objecttype) %(objectsize) %(rest)' 2>/dev/null \
       | awk '$1=="blob" && $2>100000000 {print $3}' | head -3)
if [ -n "$HUGE" ]; then
    echo "⚠ まだコミットの中に 100MB 超のファイルが残っています:"
    echo "$HUGE" | sed 's/^/    /'
    echo
    echo "  この状態では .git はほとんど縮みません（参照されているオブジェクトは消せないため）。"
    echo "  先に次を実行して、コミットからZIPを外してください:"
    echo "      bash tools/push_pages.sh          （または --no-push）"
    echo
    if [ "$YES" -ne 1 ]; then
        printf "  それでも一時ファイルの削除だけ進めますか？ [y/N] "
        read -r ans0
        case "$ans0" in y|Y|yes|YES) ;; *) echo "  中断しました。"; exit 0 ;; esac
        echo
    fi
fi

echo "▶ 現在の使用量"
du -sh .git 2>/dev/null | sed 's/^/    /'
[ -d _to_delete ] && du -sh _to_delete 2>/dev/null | sed 's/^/    /'
echo "    ----"
du -sh . 2>/dev/null | sed 's/^/    合計 /'
echo

echo "▶ 削除する対象"
[ -d _to_delete ]   && du -sh _to_delete   2>/dev/null | sed 's/^/    /'
[ -d input/hibiya ] && du -sh input/hibiya 2>/dev/null | sed 's/^/    /'
[ -d __pycache__ ]  && du -sh __pycache__  2>/dev/null | sed 's/^/    /'
DS=$(find . -name '.DS_Store' -not -path './.git/*' 2>/dev/null | wc -l | tr -d ' ')
echo "    .DS_Store  $DS 個"
echo "    .git の到達不能オブジェクト（過去にコミットしたZIPの残骸）"
echo
echo "▶ 残すもの"
for d in chiyoda-ku chuo-ku docs viewer tools data input; do
    [ -e "$d" ] && du -sh "$d" 2>/dev/null | sed 's/^/    /'
done
echo

if [ "$YES" -ne 1 ]; then
    printf "実行しますか？ [y/N] "
    read -r ans
    case "$ans" in
        y|Y|yes|YES) ;;
        *) echo "中断しました。"; exit 0 ;;
    esac
    echo
fi

# --- 1. 一時ファイルを消す --------------------------------------------------
echo "▶ 一時ファイルを削除"
rm -rf _to_delete _claude_scratch __pycache__ tools/__pycache__ 2>/dev/null
# 配布ZIPから展開した CityGML（551MB）。再変換したくなったら
#   tools/extract_hibiya.py か build_pages_min.py の手順で 12 秒ほどで戻せる。
rm -rf input/hibiya 2>/dev/null
find . -name '.DS_Store' -not -path './.git/*' -delete 2>/dev/null
find . -name '*.pyc' -not -path './.git/*' -delete 2>/dev/null
echo "    完了"

# --- 2. git の残骸を回収 ----------------------------------------------------
# 過去に ZIP をコミットした分は、コミットを取り消した後も
# 「到達不能オブジェクト」として .git に残り続ける。
# reflog を切ってから gc すると実際に消える。
echo "▶ git の到達不能オブジェクトを回収（数分かかります）"
git reflog expire --expire=now --expire-unreachable=now --all 2>/dev/null
git gc --prune=now --quiet 2>/dev/null || git gc --prune=now
echo "    完了"
echo

echo "▶ 片付け後の使用量"
du -sh .git 2>/dev/null | sed 's/^/    /'
du -sh .    2>/dev/null | sed 's/^/    合計 /'
echo
echo "  ※ chiyoda-ku/ chuo-ku/ の配布ZIP（計 5.9GB）はそのまま残しています。"
echo "     serve.py が全域表示に使う元データです。"
echo "     もう使わない場合は Finder で削除してかまいません（docs/ の公開版には影響しません）。"
