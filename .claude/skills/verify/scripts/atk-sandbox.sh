#!/usr/bin/env bash
# atkのCLIを、実ユーザーのホーム・設定・private-notes・依存環境から隔離した作業領域で実プロセスとして動かす。
# 各サブコマンドの使い方と判定は同じスキルのSKILL.mdにある。
set -euo pipefail

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "${SELF}")/../../../.." && pwd)"
LAST_PREFIX=""

usage() {
    printf '使い方: %s setup <作業領域> <証拠の置き場所> | doctor <作業領域> | run <作業領域> <ラベル> <コマンド...> | cleanup <作業領域>\n' "$0" >&2
    exit 2
}

fail() {
    printf '失敗: %s\n' "$1" >&2
    exit 2
}

require_sandbox() {
    [[ "$1" == /* && -f "$1/sandbox.env" && -f "$1/evidence-dir" ]] || fail "setupで作った作業領域の絶対パスを指定する: $1"
}

isolated() {
    local scratch="$1"
    shift
    local -a variables
    mapfile -t variables <"${scratch}/sandbox.env"
    (cd -- "${scratch}/work" && exec env -i "${variables[@]}" "$@")
}

setup() {
    local scratch="$1" evidence="$2"
    [[ "${scratch}" == /* && -d "${scratch}" && -z "$(ls -A -- "${scratch}")" ]] || fail "作業領域には空のディレクトリの絶対パスを指定する: ${scratch}"
    [[ "${evidence}" == /* && -d "${evidence}" ]] || fail "証拠の置き場所には既存のディレクトリの絶対パスを指定する: ${evidence}"
    case "${evidence}/" in "${scratch}/"*) fail "証拠の置き場所は、後始末が消す作業領域の外に置く" ;; esac
    [[ -x "${REPO_ROOT}/agent-toolkit/scripts/atk" ]] || fail "検証対象のatkが無い: ${REPO_ROOT}/agent-toolkit/scripts/atk"
    mkdir -p -- "${scratch}/bin" "${scratch}/work" "${scratch}/home/.config" "${scratch}/home/.cache" \
        "${scratch}/home/.local/share" "${scratch}/home/.local/state"
    ln -s -- "${REPO_ROOT}/agent-toolkit/scripts/atk" "${scratch}/bin/atk"
    {
        printf 'PATH=%s\n' "${scratch}/bin:${PATH}"
        printf 'LANG=C.UTF-8\n'
        printf 'HOME=%s\n' "${scratch}/home"
        printf 'XDG_CONFIG_HOME=%s\n' "${scratch}/home/.config"
        printf 'XDG_CACHE_HOME=%s\n' "${scratch}/home/.cache"
        printf 'XDG_DATA_HOME=%s\n' "${scratch}/home/.local/share"
        printf 'XDG_STATE_HOME=%s\n' "${scratch}/home/.local/state"
        printf 'AGENT_TOOLKIT_PRIVATE_NOTES=%s\n' "${scratch}/private-notes"
        printf 'AGENT_TOOLKIT_RUNTIME_DATA=%s\n' "${scratch}/runtime-data"
        printf 'GIT_CONFIG_GLOBAL=%s\n' "${scratch}/gitconfig"
        printf 'GIT_CONFIG_NOSYSTEM=1\n'
        # uvとmiseだけは実環境の取得物と信頼登録を使う。隔離すると依存とPythonを取得し直し、miseのshimが未信頼で止まる。
        printf 'UV_CACHE_DIR=%s\n' "$(uv cache dir)"
        printf 'UV_PYTHON_INSTALL_DIR=%s\n' "$(uv python dir)"
        printf 'MISE_CONFIG_DIR=%s\n' "${MISE_CONFIG_DIR:-${XDG_CONFIG_HOME:-${HOME}/.config}/mise}"
        printf 'MISE_DATA_DIR=%s\n' "${MISE_DATA_DIR:-${XDG_DATA_HOME:-${HOME}/.local/share}/mise}"
        printf 'MISE_STATE_DIR=%s\n' "${MISE_STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/mise}"
        printf 'MISE_CACHE_DIR=%s\n' "${MISE_CACHE_DIR:-${XDG_CACHE_HOME:-${HOME}/.cache}/mise}"
    } >"${scratch}/sandbox.env"
    printf '%s\n' "${evidence}" >"${scratch}/evidence-dir"
    cp -- "${scratch}/sandbox.env" "${evidence}/sandbox.env"
    isolated "${scratch}" git config --global user.name verify
    isolated "${scratch}" git config --global user.email verify@example.invalid
    isolated "${scratch}" git init --quiet --bare --initial-branch=main "${scratch}/notes-remote.git"
    isolated "${scratch}" git init --quiet --initial-branch=main "${scratch}/private-notes"
    isolated "${scratch}" git -C "${scratch}/private-notes" remote add origin "${scratch}/notes-remote.git"
    isolated "${scratch}" git -C "${scratch}/private-notes" commit --quiet --allow-empty -m init
    isolated "${scratch}" git -C "${scratch}/private-notes" push --quiet --set-upstream origin main
    # 依存環境の準備は起動の一部として済ませ、長い導入の出力は証拠へだけ残す。
    record "${scratch}" prepare atk info || fail "依存環境を準備できない。${LAST_PREFIX}.errを読む"
    printf '成功: 作業領域を準備した: %s（証拠: %s）\n' "${scratch}" "${evidence}"
}

record() {
    local scratch="$1" label="$2"
    shift 2
    [[ $# -gt 0 && "${label}" =~ ^[A-Za-z0-9._-]+$ ]] || usage
    local evidence count status=0
    evidence="$(<"${scratch}/evidence-dir")"
    count="$(find "${evidence}" -maxdepth 1 -name '*.rc' | wc -l)"
    LAST_PREFIX="$(printf '%s/%02d-%s' "${evidence}" "$((count + 1))" "${label}")"
    printf '%q ' "$@" >"${LAST_PREFIX}.cmd"
    printf '\n' >>"${LAST_PREFIX}.cmd"
    isolated "${scratch}" "$@" >"${LAST_PREFIX}.out" 2>"${LAST_PREFIX}.err" || status=$?
    printf '%s\n' "${status}" >"${LAST_PREFIX}.rc"
    printf '== %s（終了コード%s）\n' "${LAST_PREFIX}" "${status}"
    return "${status}"
}

remaining_sandbox_processes() {
    grep -lzxsF -- "HOME=$1/home" /proc/[0-9]*/environ || true
}

run() {
    local status=0 remaining file pid
    record "$@" || status=$?
    cat -- "${LAST_PREFIX}.out"
    if [[ -s "${LAST_PREFIX}.err" ]]; then
        printf -- '-- 標準エラー\n'
        cat -- "${LAST_PREFIX}.err"
    fi
    remaining="$(remaining_sandbox_processes "$1")"
    if [[ -n "${remaining}" ]]; then
        printf '注意: 前景コマンドの終了後も隔離HOMEで動くプロセスが残っている。停止は行わない: %s/home\n' "$1" >&2
        while IFS= read -r file; do
            pid="${file#/proc/}"
            printf '残存PID: %s\n' "${pid%/environ}" >&2
        done <<<"${remaining}"
    fi
    return "${status}"
}

expect_line() {
    if grep -qxF -- "$2" "${LAST_PREFIX}.out"; then
        return 0
    fi
    printf '不一致: %s（期待する行: %s）\n' "$1" "$2" >&2
    return 1
}

doctor() {
    local scratch="$1" ok=0
    run "${scratch}" doctor-info atk info || ok=1
    expect_line "plugin root" "plugin root: ${REPO_ROOT}/agent-toolkit" || ok=1
    run "${scratch}" doctor-private-notes atk config get private_notes || ok=1
    expect_line "private_notes" "${scratch}/private-notes" || ok=1
    run "${scratch}" doctor-origin git -C "${scratch}/private-notes" remote get-url origin || ok=1
    expect_line "private-notesのorigin" "${scratch}/notes-remote.git" || ok=1
    if ((ok != 0)); then
        printf '失敗: 状態確認が一致しない。操作に進まない\n' >&2
        return 1
    fi
    printf '成功: 状態確認が一致した\n'
}

cleanup() {
    local scratch="$1" evidence remaining file pid
    evidence="$(<"${scratch}/evidence-dir")"
    remaining="$(remaining_sandbox_processes "${scratch}")"
    if [[ -n "${remaining}" ]]; then
        printf '失敗: 作業領域の環境で動くプロセスが残っている。起動した時の識別子で止めてから再実行する\n' >&2
        while IFS= read -r file; do
            pid="${file#/proc/}"
            ps -o pid=,args= -p "${pid%/environ}" >&2 || true
        done <<<"${remaining}"
        return 1
    fi
    rm -r -- "${scratch}"
    if [[ -e "${scratch}" ]]; then
        printf '失敗: 作業領域が残っている: %s\n' "${scratch}" >&2
        return 1
    fi
    printf '成功: 作業領域を消した: %s\n' "${scratch}"
    printf '証拠: %s（コマンド%s件）\n' "${evidence}" "$(find "${evidence}" -maxdepth 1 -name '*.rc' | wc -l)"
}

[[ $# -ge 2 ]] || usage
command="$1"
shift
case "${command}" in
setup)
    [[ $# -eq 2 ]] || usage
    setup "$1" "$2"
    ;;
doctor)
    [[ $# -eq 1 ]] || usage
    require_sandbox "$1"
    doctor "$1"
    ;;
run)
    [[ $# -ge 3 ]] || usage
    require_sandbox "$1"
    run "$@"
    ;;
cleanup)
    [[ $# -eq 1 ]] || usage
    require_sandbox "$1"
    cleanup "$1"
    ;;
*) usage ;;
esac
