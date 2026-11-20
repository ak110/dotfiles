#!/usr/bin/env bash
# 管理Pythonを導入する前にも使う。各段の直近の明細はCIも回収する状態ディレクトリへ置く。
write_uv_output() {
    local stage=$1 code=$2 output=$3
    [[ -n "$output" || "$code" != 0 ]] || return 0
    local directory="${XDG_STATE_HOME:-$HOME/.local/state}/agent-toolkit"
    if ((code != 0)); then stage+=-failed; fi
    local log="$directory/uv-$stage.log" line saved='' summary='' cause='' visible='' routine
    local rules="${BASH_SOURCE[0]%/*}/uv_output_patterns.txt" sensitive actionable
    # 両OS共通の正規表現。保存時に伏せる行、失敗時にも表示する行、残りは既知の正常行。
    local -a patterns
    if ! {
        IFS= read -r sensitive && IFS= read -r actionable && mapfile -t patterns
    } <"$rules"; then
        printf '明細を分類できません: %s。更新ファイルを確認してください。\n%s\n' "$rules" "$output" >&2
        return 0
    fi
    local IFS='|'
    routine="^(${patterns[*]})$"
    local continuation=0
    while IFS= read -r line; do
        if [[ "${line,,}" =~ $sensitive || "$output" == *'PRIVATE KEY-----'* ]]; then
            saved+='[REDACTED]'$'\n'
        else
            saved+="$line"$'\n'
        fi
        if [[ -z "$line" || "$line" =~ $routine ]]; then
            continuation=0
        elif [[ "$line" =~ $actionable || ($continuation == 1 && "$line" == ' '*) ]]; then
            visible+="$line"$'\n'
            continuation=1
        else
            continuation=0
            summary=$line
            if [[ "$line" == *'cause:'* || "$line" == 'error:'* ]]; then cause=$line; fi
        fi
    done <<<"$output"
    local temp=''
    if { [[ -d "$directory" ]] || mkdir -p -- "$directory"; } &&
        temp=$(mktemp -- "$log.XXXXXXXX") &&
        printf '%(%FT%T%z)T exit=%s\n%s' -1 "$code" "$saved" >"$temp" &&
        mv -fT -- "$temp" "$log"; then
        if ((code != 0)); then
            printf '%s\n' "${cause:-${summary:-exit=$code}} (詳細: $log)" >&2
        fi
        printf '%s' "$visible" >&2
    else
        [[ -z "$temp" ]] || rm -f -- "$temp" || :
        printf '明細を保存できません: %s。書込権限と空き容量を確認してください。\n%s\n' "$log" "$output" >&2
    fi
    return 0
}
