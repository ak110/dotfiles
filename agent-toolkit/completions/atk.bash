# `atk`コマンドのbash補完（argcomplete）を実行時登録する。
# `register-python-argcomplete`はargcomplete同梱のCLIで、対象コマンドの
# `# PYTHON_ARGCOMPLETE_OK`マーカーを検出して補完関数を動的生成する。
# `atk`・`register-python-argcomplete`いずれか未実在の環境でも安全に読み込めるようガードする。
if command -v atk >/dev/null 2>&1 && command -v register-python-argcomplete >/dev/null 2>&1; then
    eval "$(register-python-argcomplete atk)"
fi
