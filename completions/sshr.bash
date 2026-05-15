# sshrのbash補完。ssh本体の補完関数_sshへ委譲する。
_sshr() {
    if ! declare -F _ssh >/dev/null 2>&1; then
        if declare -F _completion_loader >/dev/null 2>&1; then
            _completion_loader ssh
        fi
    fi
    if declare -F _ssh >/dev/null 2>&1; then
        _ssh
    fi
}
complete -F _sshr sshr
