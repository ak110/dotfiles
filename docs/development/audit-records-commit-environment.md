# 監査記録: 検証環境の同期

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/skills/atk-mode/playbooks/publish.md：検証とCI：2026年10月11日

uv 0.13.0で、`UV_PROJECT_ENVIRONMENT`で隔離した仮想環境を使い、dotfilesのリポジトリ直下を`UV_FROZEN=1`のもとで同期した。
リポジトリ直下をPython 3.15で`uv sync --all-groups --all-extras`した環境へ、ロックファイルに無い`toolz`を`uv pip install`で加えた。
この環境では`uv run`の後も`import toolz`が成功し、続く`uv sync --all-groups --all-extras`の後は`ModuleNotFoundError`になった。
2026年10月15日、uv 0.13.1で同じ観測を取り直し、同じ結果だった。

仮想環境の版の保持と再構築は、対象プロジェクト自身の`.python-version`の有無、`UV_PYTHON`の指定、`--python`の明示で結果が変わる。
条件ごとの観測は次節の表にある。
`agent-toolkit/`は`.python-version`を持たないため、`--python`を渡さない同期は既存の仮想環境をリポジトリ直下の基準版で構築し直さない。
その後`agent-toolkit/pyproject.toml`の`requires-python`が`>=3.15`となり、`agent-toolkit/`を3.14で同期する手順は解決の不一致で終了コード2となって成立しなくなった。
このため版の保持は、`requires-python`が`>=3.14`で依存を持たない一時プロジェクトを`.python-version`（`3.15`）を置いたディレクトリの直下に作成し、`agent-toolkit/`と同じ配置で再現した。

余分なパッケージの残存と除去の再検証手順を次に示す。
`WORKTREE`にはdotfilesの作業ツリーの絶対パス、`TMP_DIR`には作業ツリーの外に用意した空の一時ディレクトリの絶対パスを設定する。
`UV_FROZEN=1`を設定したシェルで、コードブロックの行を順に実行する。
`uv run`で実行した`import toolz`が`toolz present`を出力し、続く`uv sync`の後に`$ROOT_ENV/bin/python`で実行した`import toolz`が`ModuleNotFoundError`で失敗すれば、再現できている。
版の保持と再構築は、次節の手順で確かめる（`.python-version`が親だけにある行は、`.python-version`を置かないプロジェクトを親の`3.15`の直下へ作って同じ操作を実行する）。

```sh
ROOT_ENV="$TMP_DIR/root-env"
UV_PROJECT_ENVIRONMENT="$ROOT_ENV" uv sync --project "$WORKTREE" --all-groups --all-extras --python 3.15
uv pip install --python "$ROOT_ENV/bin/python" toolz
UV_PROJECT_ENVIRONMENT="$ROOT_ENV" uv run --project "$WORKTREE" python -c 'import toolz; print("toolz present")'
UV_PROJECT_ENVIRONMENT="$ROOT_ENV" uv sync --project "$WORKTREE" --all-groups --all-extras --python 3.15
"$ROOT_ENV/bin/python" -c 'import toolz'
```

## agent-toolkit/skills/atk-mode/playbooks/publish.md：検証とCI：2026年10月19日

2026年10月19日にuv 0.13.1で、依存を持たず`requires-python`が`>=3.14`の一時プロジェクトへ`.python-version`（`3.15`）と`uv.lock`を置き、3.14の仮想環境から始めて次を観測した。

| 対象プロジェクトの`.python-version` | `UV_PYTHON` | 操作 | 既存3.14環境の結果 |
| --- | --- | --- | --- |
| なし（親だけ3.15） | 未指定 | `uv run`、`uv sync` | 3.14を維持 |
| なし（親だけ3.15） | 未指定 | `uv sync --python 3.15` | 3.15に再作成 |
| 3.15 | 未指定 | `uv run --frozen` | 3.15に再作成 |
| 3.15 | 3.14 | `uv run --frozen`、`uv sync --frozen` | 3.14を維持 |
| 3.15 | 3.14 | `uv sync --frozen --python 3.15` | 3.15に再作成 |

`uv pip install six`で加えたパッケージは`uv run --frozen`の後も残り、`uv sync --frozen`で除かれた。

再検証は次を順に実行する。
最初の`cd`で作業ツリーの外に空の一時ディレクトリを作成して移動し、以降のファイル作成と`uv`の呼び出しはそのディレクトリだけを対象にする。
`uv run`の後の版が3.15、`UV_PYTHON=3.14`の2操作の後が3.14、`--python "$(cat .python-version)"`の後が3.15なら、記述どおりである。
処理時点のuvで版が異なれば、その版の観測へ記述を合わせる。

```sh
cd "$(mktemp -d)"
unset UV_PYTHON VIRTUAL_ENV
printf '[project]\nname = "observe"\nversion = "0.0.0"\nrequires-python = ">=3.14"\ndependencies = []\n' > pyproject.toml
echo 3.15 > .python-version
uv lock
uv venv --python 3.14 .venv
uv run --frozen python -c 'import sys; print(sys.version_info[:2])'
rm -rf .venv && uv venv --python 3.14 .venv
UV_PYTHON=3.14 uv run --frozen python -c 'import sys; print(sys.version_info[:2])'
UV_PYTHON=3.14 uv sync --frozen && .venv/bin/python -c 'import sys; print(sys.version_info[:2])'
UV_PYTHON=3.14 uv sync --frozen --python "$(cat .python-version)" && .venv/bin/python -c 'import sys; print(sys.version_info[:2])'
```
