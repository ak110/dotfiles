#!/usr/bin/env bash
# 管理Python自身を起動する前に、ランタイムの更新を試みる。
set -euo pipefail
uv_path=$1
root=${BASH_SOURCE[0]%/*}
export UPDATE_DOTFILES_BOOTSTRAP_FAILURES=''
if ! command -v timeout >/dev/null; then
    printf '%s\n' 'timeoutが見つからないため更新を開始できません。coreutilsを導入して再実行してください。' >&2
    exit 127
fi
# 外側は端末の前景でCtrl+Cと期限を内側へ渡す。内側のtimeoutは段専用の
# プロセス群を作り、応答しない子には10秒後にKILLを送る。
# waitを担当する段が子孫を止めて戻るまで、呼出し元は終了しない。
bootstrap_interrupted=0
trap 'bootstrap_interrupted=1' INT
timed_stage() {
    local name=$1 limit=$2
    shift 2
    local wait_hint=''
    case "$name" in 管理Pythonの更新 | 管理Pythonの導入) wait_hint='uvキャッシュの整理中は待つ場合があります。' ;; esac
    printf '%s: %sCtrl+Cで中断できます。\n' "$name" "$wait_hint" >&2
    local code=0
    ((bootstrap_interrupted == 0)) || return 130
    timeout --foreground "$limit" bash "$root/update_stage.sh" "$@" || code=$?
    ((bootstrap_interrupted == 0)) || return 130
    return "$code"
}
run_stage() {
    local name=$1 classification=$2 limit=$3
    shift 3
    local code=0
    timed_stage "$name" "$limit" "$@" || code=$?
    ((code != 130)) || exit 130
    if ((code != 0)); then
        UPDATE_DOTFILES_BOOTSTRAP_FAILURES+="$name: exit=$code"$'\n'
        if [[ "$name" == 'uvの自己更新' ]]; then
            printf '%s\n' 'uvの自己更新に失敗しました。既存のuvでdotfiles更新を実行し、次回のupdate-dotfiles起動時に自己更新を再試行します。対応不要（dotfilesの更新処理は継続しました）。' >&2
        else
            printf '%s\n' "$name に失敗しました（exit=$code）。既存版で更新を続け、次回起動時に再試行します。" >&2
        fi
        [[ "$classification" != abort ]] || return "$code"
    fi
}
if [[ ! -x "$uv_path" ]]; then
    # インストーラーの取得・実行は既存のupdate_uv.shと同じ公式経路を使う。
    install_uv() {
        set -o pipefail
        export UV_INSTALL_DIR="${uv_path%/*}" UV_NO_MODIFY_PATH=1
        unset UV_UNMANAGED_INSTALL
        curl --fail --silent --show-error --location --max-time 120 https://astral.sh/uv/install.sh | sh
    }
    export -f install_uv
    export uv_path
    if ! run_stage 'uvの導入' abort 300 bash -c install_uv; then
        run_stage 'uvの導入' abort 300 bash -c install_uv || true
    fi
    if [[ ! -x "$uv_path" ]]; then
        printf '%s\n' 'uvを導入できません。https://docs.astral.sh/uv/getting-started/installation/ の公式手順で導入しupdate-dotfilesを再実行してください。' >&2
        exit 127
    fi
fi
if [[ "${AGENT_TOOLKIT_PROCESS_LOOP_SESSION:-}" != 1 ]]; then
    run_stage 'uvの自己更新' continue 300 bash "$root/update_uv.sh" "$uv_path"
    run_stage '管理Pythonの更新' continue 900 "$uv_path" python install --upgrade 3.15
fi
# 前回の更新が始めたuvの整理がキャッシュを排他している間も待たないよう、キャッシュを使わずに解決する。
find_python() {
    timed_stage '管理Pythonの検索' 60 "$uv_path" python find --no-cache 3.15
}
python_path="$(find_python)" || {
    [[ $? != 130 ]] || exit 130
    run_stage '管理Pythonの導入' abort 900 "$uv_path" python install 3.15 ||
        run_stage '管理Pythonの導入' abort 900 "$uv_path" python install 3.15
    python_path="$(find_python)"
}
if [[ "$UPDATE_DOTFILES_BOOTSTRAP_FAILURES" == *'管理Pythonの更新'* && "$python_path" =~ 3\.15\.[0-9]+(a|b|rc)[0-9]+ ]]; then
    printf '%s\n' "残った管理Pythonはpre-releaseです: $python_path。次回起動時に更新を再試行します。" >&2
fi
trap - INT
((bootstrap_interrupted == 0)) || exit 130
