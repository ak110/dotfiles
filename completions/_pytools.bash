# 自動生成ファイル。scripts/gen-completions.py が出力する。手編集禁止。
# 再生成: `uv run --script scripts/gen-completions.py`
#
# argcomplete対応の`pytools`系コマンドにbash補完を提供する。
# 補完起動時に`_ARGCOMPLETE=1`等の環境変数を渡してコマンド本体を再起動し、
# argcomplete側で候補生成と出力を行う。

_python_argcomplete() {
    local IFS=$'\013'
    local SUPPRESS_SPACE=0
    if compopt +o nospace 2> /dev/null; then
        SUPPRESS_SPACE=1
    fi
    COMPREPLY=( $(IFS="$IFS" \
                  COMP_LINE="$COMP_LINE" \
                  COMP_POINT="$COMP_POINT" \
                  COMP_TYPE="$COMP_TYPE" \
                  _ARGCOMPLETE_COMP_WORDBREAKS="$COMP_WORDBREAKS" \
                  _ARGCOMPLETE=1 \
                  _ARGCOMPLETE_SUPPRESS_SPACE=$SUPPRESS_SPACE \
                  "$1" 8>&1 9>&2 1>/dev/null 2>/dev/null) )
    if [[ $? != 0 ]]; then
        unset COMPREPLY
    elif [[ $SUPPRESS_SPACE == 1 ]] && [[ "${COMPREPLY-}" =~ [=/:]$ ]]; then
        compopt -o nospace
    fi
}

complete -o nospace -o default -o bashdefault -F _python_argcomplete EcoUtilities
complete -o nospace -o default -o bashdefault -F _python_argcomplete ccommit
complete -o nospace -o default -o bashdefault -F _python_argcomplete check-image-sizes
complete -o nospace -o default -o bashdefault -F _python_argcomplete claude-plans-viewer
complete -o nospace -o default -o bashdefault -F _python_argcomplete claude-session-export
complete -o nospace -o default -o bashdefault -F _python_argcomplete claudize
complete -o nospace -o default -o bashdefault -F _python_argcomplete clonedir
complete -o nospace -o default -o bashdefault -F _python_argcomplete codexize
complete -o nospace -o default -o bashdefault -F _python_argcomplete colloquial-checker
complete -o nospace -o default -o bashdefault -F _python_argcomplete dateRelocator
complete -o nospace -o default -o bashdefault -F _python_argcomplete deletehomonym
complete -o nospace -o default -o bashdefault -F _python_argcomplete dirsize
complete -o nospace -o default -o bashdefault -F _python_argcomplete dotfiles-media-remote
complete -o nospace -o default -o bashdefault -F _python_argcomplete feedback-add
complete -o nospace -o default -o bashdefault -F _python_argcomplete git-justify
complete -o nospace -o default -o bashdefault -F _python_argcomplete markdown-viewer
complete -o nospace -o default -o bashdefault -F _python_argcomplete mvdir
complete -o nospace -o default -o bashdefault -F _python_argcomplete psgrep
complete -o nospace -o default -o bashdefault -F _python_argcomplete py-imageconverter
complete -o nospace -o default -o bashdefault -F _python_argcomplete py-pdf-to-image
complete -o nospace -o default -o bashdefault -F _python_argcomplete py-rename
complete -o nospace -o default -o bashdefault -F _python_argcomplete py-rmdirs
complete -o nospace -o default -o bashdefault -F _python_argcomplete randfile
complete -o nospace -o default -o bashdefault -F _python_argcomplete releaser
complete -o nospace -o default -o bashdefault -F _python_argcomplete rename2hash
complete -o nospace -o default -o bashdefault -F _python_argcomplete repack-archive
complete -o nospace -o default -o bashdefault -F _python_argcomplete touch-file
