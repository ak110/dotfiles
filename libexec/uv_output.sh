#!/usr/bin/env bash
# 管理Pythonの導入前にも使う。前景のコマンドの出力を読みながら、対処が必要な行を端末へ渡す。
with_uv_output() (
    local stage=$1
    shift
    local directory="${XDG_STATE_HOME:-$HOME/.local/state}/agent-toolkit"
    local rules="${BASH_SOURCE[0]%/*}/uv_output_patterns.txt" sensitive actionable routine
    local -a patterns
    if ! { IFS= read -r sensitive && IFS= read -r actionable && mapfile -t patterns; } <"$rules"; then
        printf '明細を分類できません: %s。更新ファイルを確認してください。\n' "$rules" >&2
        routine=''
    else
        local IFS='|'
        routine="^(${patterns[*]})$"
    fi
    local saved='' hidden='' summary='' cause='' continuation=0 private_key=0
    local interrupted=0 code=0 line plain sgr=$'\e''\[[0-9;:]*m'
    trap 'interrupted=1' INT
    # lastpipeで読み手をこのシェルに置く。INTでuvが終了してもパイプに残る末尾まで読む。
    set +e +m
    shopt -s lastpipe
    # shellcheck disable=SC2329 # 呼出し元の段がこの関数を呼び、コマンドの出力を同じ記録に追記する。
    capture_uv_command() {
        "$@" 2>&1 | {
            while IFS= read -r line || [[ -n "$line" ]]; do
                plain=${line%$'\r'}
                while [[ "$plain" =~ $sgr ]]; do plain=${plain//"${BASH_REMATCH[0]}"/}; done
                # shellcheck disable=SC2034 # stage_attemptが修復の要否をこの値から判定する。
                if [[ -n "${match_text:-}" && "$plain" == *"$match_text"* ]]; then matched=1; fi
                if [[ -z "$routine" ]]; then
                    printf '%s\n' "$line" >&2
                    continue
                fi
                if [[ "${plain,,}" =~ $sensitive ]]; then saved+='[REDACTED]'$'\n'; else saved+="$line"$'\n'; fi
                if [[ "$plain" == *'PRIVATE KEY-----'* ]]; then private_key=1; fi
                if [[ -n "$plain" && ! "$plain" =~ $routine ]]; then
                    if [[ "$plain" =~ $actionable || ($continuation == 1 && "$plain" == ' '*) ]]; then
                        printf '%s\n' "$line" >&2
                        continuation=1
                        continue
                    fi
                    summary=$line
                    if [[ "$plain" == *'cause:'* || "$plain" == 'error:'* ]]; then cause=$line; fi
                fi
                continuation=0
                hidden+="$line"$'\n'
            done
        }
        local status=${PIPESTATUS[0]}
        ((interrupted == 0)) || return 130
        return "$status"
    }
    "$@" || code=$?
    if ((interrupted)); then code=130; fi
    [[ -n "$routine" ]] || return "$code"
    [[ -n "$saved" || "$code" != 0 ]] || return 0
    if ((code != 0)); then stage+=-failed; fi
    local log="$directory/uv-$stage.log" temp=''
    if ((private_key)); then saved=$'[REDACTED]\n'; fi
    if { [[ -d "$directory" ]] || mkdir -p -- "$directory"; } &&
        temp=$(mktemp -- "$log.XXXXXXXX") &&
        printf '%(%FT%T%z)T exit=%s\n%s' -1 "$code" "$saved" >"$temp" &&
        mv -fT -- "$temp" "$log"; then
        if ((code != 0)); then printf '%s\n' "${cause:-${summary:-exit=$code}} (詳細: $log)" >&2; fi
    else
        [[ -z "$temp" ]] || rm -f -- "$temp" || :
        printf '明細を保存できません: %s。書込権限と空き容量を確認してください。\n%s' "$log" "$hidden" >&2
    fi
    return "$code"
)
