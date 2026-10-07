# 撤去前の配布設定

`pytools/post_apply_test.py`が、未編集の配布設定の削除と編集済み設定の保護を検証するための入力である。
浅いcheckoutでも実行できるように、撤去前の内容をバイト列のまま保存する。
内容を整形すると撤去表が持つ元の内容と一致しなくなるため、改行や末尾の空白も保持する。
wheelへの混入を避けるため、`pytools/`の外へ配置する。

| fixture | 取得元のGit object |
| --- | --- |
| `ipython_kernel_config.py.txt` | `89ce8990a:.chezmoi-source/dot_ipython/profile_ipy/ipython_kernel_config.py` |
| `screenrc.txt` | `d074bebbe:.chezmoi-source/dot_screenrc` |
| `xonsh_rc.xsh.txt` | `d074bebbe:.chezmoi-source/dot_config/xonsh/rc.xsh` |
| `yapf_style.txt` | `d074bebbe:.chezmoi-source/dot_config/yapf/style` |
| `pypoetry_config.toml.txt` | `d074bebbe:.chezmoi-source/dot_config/pypoetry/config.toml` |
| `rest_client_environment.json.txt` | `d074bebbe:.chezmoi-source/dot_config/rest-client/environment.json`（空ファイル） |
