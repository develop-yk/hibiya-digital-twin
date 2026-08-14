#!/bin/bash
# =============================================================================
# GitHub へ安全に push する
#
#   bash tools/push_pages.sh
#
# やること:
#   1. 配布ZIP（1ファイル 0.8〜2.4GB）を含むローカルコミットを取り消す
#      → GitHub は 100MB/ファイルを超えると必ず拒否するため
#   2. .gitignore を効かせた状態でインデックスを作り直す
#   3. 100MB 超のファイルが残っていないか検査（残っていれば中断）
#   4. docs/ をレイヤー単位の小さなコミットに分割
#   5. 1コミットずつ push（途中で失敗しても、再実行すれば続きから）
#
# 作業ツリーのファイルは一切消しません。git の記録から外すだけです。
# =============================================================================
#
# オプション:
#   --no-push   コミットの作り直しまでで止める。push は GitHub Desktop から行う。
#               （ターミナルの git に GitHub の認証情報が無い場合はこちら）
# =============================================================================
set -u

DO_PUSH=1
for a in "$@"; do
    case "$a" in
        --no-push) DO_PUSH=0 ;;
        *) echo "不明なオプション: $a"; exit 1 ;;
    esac
done

cd "$(dirname "$0")/.." || exit 1
REPO=$(pwd)
echo "リポジトリ: $REPO"
echo

# --- 大きな push で切断されにくくする ---------------------------------------
git config http.postBuffer 524288000     # 500MB
git config http.lowSpeedLimit 1000       # 1KB/s を
git config http.lowSpeedTime 600         # 10分下回ったら諦める
git config core.compression 9
git config core.quotepath false          # 日本語ファイル名を検査から取りこぼさない

# --- 0. .gitignore の存在確認 -----------------------------------------------
if [ ! -f .gitignore ]; then
    echo "  ✗ .gitignore がありません。これが無いと配布ZIPが除外されず push が失敗します。"
    echo "    リポジトリ直下に .gitignore を置いてから、もう一度実行してください。"
    exit 1
fi

# --- 0. リモートの現在地を取得 ----------------------------------------------
echo "▶ リモートの状態を取得"
if git fetch origin main 2>/dev/null; then
    BASE=$(git rev-parse FETCH_HEAD)
else
    echo "  ! git fetch できませんでした。手元の origin/main を基準にします。"
    BASE=$(git rev-parse origin/main 2>/dev/null) || {
        echo "  ✗ origin/main が分かりません。GitHub Desktop で一度 Fetch してください。"
        exit 1
    }
fi
echo "  origin/main = $BASE"
echo

# --- 1. まだ push していないコミットを取り消す ------------------------------
AHEAD=$(git rev-list --count "$BASE..HEAD" 2>/dev/null || echo 0)
if [ "$AHEAD" -gt 0 ]; then
    echo "▶ 未pushのコミット $AHEAD 件を取り消します（ファイルは消えません）"
    git reset --mixed "$BASE" || exit 1
fi

# --- 2. .gitignore を効かせてインデックスを作り直す --------------------------
echo "▶ インデックスを作り直します（.gitignore を反映）"
git rm -r --cached . -q 2>/dev/null
git add -A || exit 1

# --- 3. 巨大ファイルが残っていないか検査 ------------------------------------
echo "▶ 100MB 超のファイルが含まれていないか検査"
BIG=$(git diff --cached --name-only | while read -r f; do
        [ -f "$f" ] || continue
        sz=$(wc -c < "$f")
        [ "$sz" -gt 100000000 ] && printf '%10d  %s\n' "$sz" "$f"
      done)
if [ -n "$BIG" ]; then
    echo "  ✗ まだ 100MB 超のファイルが含まれています:"
    echo "$BIG"
    echo
    echo "  .gitignore に追記してから、もう一度このスクリプトを実行してください。"
    exit 1
fi
TOTAL=$(git diff --cached --name-only | wc -l | tr -d ' ')
echo "  OK  （$TOTAL ファイル）"
echo

# --- 4. コミットを小分けに作る（ネットワーク不要） --------------------------
echo "▶ コミットを作成"
git reset -q          # いったん全部アンステージして、順番に積み直す

commit_group () {     # commit_group "メッセージ" パス...
    msg=$1; shift
    for p in "$@"; do [ -e "$p" ] && git add -- "$p"; done
    if git diff --cached --quiet; then return 0; fi
    n=$(git diff --cached --name-only | wc -l | tr -d ' ')
    git commit -q -m "$msg" || exit 1
    echo "  + $msg  （$n ファイル）"
}

commit_group "ビューアと配信サーバ" \
    .gitignore .gitattributes README.md PUBLISH.md serve.py viewer tools data preview.png
commit_group "docs: サイト本体と CesiumJS 同梱版" \
    docs/index.html docs/catalog.json docs/.nojekyll docs/vendor

for d in docs/tiles/*/*; do
    [ -d "$d" ] || continue
    commit_group "docs: $(basename "$(dirname "$d")")/$(basename "$d")" "$d"
done

# 取りこぼし（新しいレイヤーを足した場合など）
commit_group "docs: その他" docs
commit_group "残りのファイル" .

echo

if [ "$DO_PUSH" -eq 0 ]; then
    N=$(git rev-list --count "$BASE..HEAD")
    echo "▶ コミットの作り直しまで完了しました（--no-push 指定のため push はしません）"
    echo
    echo "  $N 件のコミットが push 待ちです。GitHub Desktop の «Push origin» を押してください。"
    echo "  配布ZIP はもう含まれていないので、送信量は約 170MB です。"
    exit 0
fi

# --- 5. 1コミットずつ push（失敗しても再実行で続きから）---------------------
PENDING=$(git rev-list --count "$BASE..HEAD")
echo "▶ $PENDING 件のコミットを1つずつ push します"
echo

i=0
for sha in $(git rev-list --reverse origin/main..HEAD); do
    i=$((i + 1))
    subject=$(git log -1 --format=%s "$sha")
    printf "  [%d/%d] %s … " "$i" "$PENDING" "$subject"
    ok=0
    for attempt in 1 2 3; do
        if git push -q origin "$sha:refs/heads/main" 2>/tmp/push_err.txt; then
            ok=1; break
        fi
        printf "再試行%d " "$attempt"
        sleep 3
    done
    if [ "$ok" -ne 1 ]; then
        echo "失敗"
        echo
        echo "--- git のエラー出力 ---"
        cat /tmp/push_err.txt
        echo "------------------------"
        echo "ここまでは GitHub に反映されています。"
        echo "もう一度このスクリプトを実行すれば続きから再開します。"
        echo
        echo "認証を求められて進めない場合は、コミットの作り直しだけ済ませて"
        echo "GitHub Desktop から push してください:"
        echo "    bash tools/push_pages.sh --no-push"
        exit 1
    fi
    echo "OK"
done

echo
echo "▶ 完了。GitHub に反映されました。"
echo
echo "  Settings → Pages → Source を"
echo "    Deploy from a branch / Branch: main / folder: /docs"
echo "  にすると、1〜2分でここに公開されます:"
echo "    https://develop-yk.github.io/hibiya-digital-twin/"
echo
echo "  ※ .git が肥大している場合（du -sh .git で数GB）は、"
echo "     不要になった過去のZIPを削除して容量を戻せます:"
echo "       git reflog expire --expire=now --all && git gc --prune=now"
