# shellcheck shell=bash
# NOTE: 対応するWindows版 → bin/_claude-wrapper.cmd
# bin/sonnet・bin/opus・bin/fableが`source`で読み込む起動処理。
# 単体では起動せず、呼び出し側がモデル固有の引数を渡して`run_claude`を呼ぶ。
# 直接実行しないためshebangを持たない。

# 対話セッションかどうかを判定する。
# 非対話と確定できるのは、応答を標準出力へ書いて終了するオプションだけとする。
# 位置引数は対話TUIの初期プロンプト（`sonnet "実装して"`）でも現れるため根拠にしない。
is_interactive_invocation() {
    local arg
    for arg in "$@"; do
        case "$arg" in
        --help | -h | --version | -v | --print | -p) return 1 ;;
        *) ;;
        esac
    done
    return 0
}

# claude実体のパスを解決する。
# `$HOME/.local/bin/claude`はAnthropic公式インストーラーの既定配置であり、
# npm版など別経路への切り替わりを防ぐためこれを優先する。
# 当該パスに実行可能ファイルが無い環境ではPATH探索へ委ねる。
resolve_claude_bin() {
    local preferred="$HOME/.local/bin/claude"
    if [ -x "$preferred" ]; then
        printf '%s\n' "$preferred"
        return 0
    fi
    command -v claude
}

# claudeを起動し、対話セッションが正常終了した場合だけ画面をクリアする。
# 異常終了時にクリアすると、標準エラーへ出力した診断情報を利用者が確認できない。
run_claude() {
    local claude_bin rc
    claude_bin="$(resolve_claude_bin || true)"
    if [ -z "$claude_bin" ]; then
        printf '%s\n' 'claudeコマンドが見つかりません。' >&2
        exit 127
    fi
    local interactive=1
    is_interactive_invocation "$@" || interactive=0
    "$claude_bin" "$@"
    rc=$?
    if [ "$interactive" -eq 1 ] && [ "$rc" -eq 0 ] && [ -t 1 ] && [ -t 2 ]; then
        "$(dirname "${BASH_SOURCE[0]}")/c"
    fi
    exit $rc
}
