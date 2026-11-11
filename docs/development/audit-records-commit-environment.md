# 監査記録: 検証環境の同期

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/skills/commit/references/publish.md：検証とCI：2026年10月11日

uv 0.13.0で、`UV_PROJECT_ENVIRONMENT`で隔離した仮想環境を使い、dotfilesのリポジトリ直下と`agent-toolkit/`のプロジェクトを`UV_FROZEN=1`のもとで同期した。
リポジトリ直下をPython 3.15で`uv sync --all-groups --all-extras`した環境へ、ロックファイルに無い`toolz`を`uv pip install`で加えた。
この環境では`uv run`の後も`import toolz`が成功し、続く`uv sync --all-groups --all-extras`の後は`ModuleNotFoundError`になった。
`agent-toolkit/`をPython 3.14で構築した環境は、`--python`を付けない`uv sync`と`uv run`の後も3.14のままで、`uv sync --python 3.15`の後は3.15になった。
`agent-toolkit/`は`.python-version`を持たないため、`--python`を渡さない同期は既存の仮想環境をリポジトリ直下の基準版で構築し直さない。

その後`agent-toolkit/pyproject.toml`の`requires-python`が`>=3.15`となり、`agent-toolkit/`を3.14で同期する手順は解決の不一致で終了コード2となって成立しなくなった。
2026年10月15日、uv 0.13.1で同じ観測を取り直した。
余分なパッケージの残存と除去はリポジトリ直下で上記と同じ結果になった。
版の保持と再構築は、`requires-python`が`>=3.14`で依存を持たない一時プロジェクトを`.python-version`（`3.15`）を置いたディレクトリの直下に作成し、`agent-toolkit/`と同じ配置で再現した。
3.14で構築した環境は`--python`を付けない`uv sync`と`uv run`の後も`(3, 14)`のままで、`uv sync --python 3.15`の後に`(3, 15)`となった。

再検証の手順を次に示す。
`WORKTREE`にはdotfilesの作業ツリーの絶対パスを設定する。
`TMP_DIR`には作業ツリーの外に用意した空の一時ディレクトリの絶対パスを設定する。
`UV_FROZEN=1`を設定したシェルで、コードブロックの行を順に実行する。
`ROOT_ENV`はリポジトリ直下のプロジェクト、`SUB_ENV`は一時プロジェクトの仮想環境で、各`uv`の呼び出しへ`UV_PROJECT_ENVIRONMENT`で渡す。
`uv run`で実行した`import toolz`が`toolz present`を出力し、続く`uv sync`の後に`$ROOT_ENV/bin/python`で実行した`import toolz`が`ModuleNotFoundError`で失敗すれば、余分なパッケージの残存と除去を再現できている。
一時プロジェクトの版が`--python`を付けない同期と`uv run`の後に`(3, 14)`のままで、`--python 3.15`の同期の後に`(3, 15)`となれば、版の保持と再構築を再現できている。

```sh
ROOT_ENV="$TMP_DIR/root-env"
SUB_ENV="$TMP_DIR/sub-env"
SUB_PROJECT="$TMP_DIR/parent/sub"
UV_PROJECT_ENVIRONMENT="$ROOT_ENV" uv sync --project "$WORKTREE" --all-groups --all-extras --python 3.15
uv pip install --python "$ROOT_ENV/bin/python" toolz
UV_PROJECT_ENVIRONMENT="$ROOT_ENV" uv run --project "$WORKTREE" python -c 'import toolz; print("toolz present")'
UV_PROJECT_ENVIRONMENT="$ROOT_ENV" uv sync --project "$WORKTREE" --all-groups --all-extras --python 3.15
"$ROOT_ENV/bin/python" -c 'import toolz'
mkdir -p "$SUB_PROJECT"
printf '3.15\n' > "$TMP_DIR/parent/.python-version"
printf '[project]\nname = "sub"\nversion = "0.1.0"\nrequires-python = ">=3.14"\ndependencies = []\n' > "$SUB_PROJECT/pyproject.toml"
UV_FROZEN=0 uv lock --project "$SUB_PROJECT"
UV_PROJECT_ENVIRONMENT="$SUB_ENV" uv sync --project "$SUB_PROJECT" --python 3.14
UV_PROJECT_ENVIRONMENT="$SUB_ENV" uv sync --project "$SUB_PROJECT"
UV_PROJECT_ENVIRONMENT="$SUB_ENV" uv run --project "$SUB_PROJECT" python -c 'import sys; print(sys.version_info[:2])'
UV_PROJECT_ENVIRONMENT="$SUB_ENV" uv sync --project "$SUB_PROJECT" --python 3.15
"$SUB_ENV/bin/python" -c 'import sys; print(sys.version_info[:2])'
```

## agent-toolkit/skills/commit/references/publish.md：検証とCI：2026年10月19日

前節の観測は`.python-version`を持たないプロジェクトの条件であり、`.python-version`を持つプロジェクトでは`uv run`の自動同期が既存の仮想環境を再作成する。
2026年10月19日にuv 0.13.1で、依存を持たず`requires-python`が`>=3.14`の一時プロジェクトへ`.python-version`（`3.15`）と`uv.lock`を置き、3.14の仮想環境から始めて次を観測した。

- 版指定なし: `uv run --frozen`の後に3.15で再作成された
- `UV_PYTHON=3.14`: `uv run --frozen`と`--python`なしの`uv sync --frozen`の後も3.14のままだった
- `UV_PYTHON=3.14`のまま`uv sync --frozen --python "$(cat .python-version)"`: 3.15で再作成された
- 対照として`.python-version`を除くと、版指定なしの`uv run --frozen`の後も3.14のままだった
- `uv pip install six`で加えたパッケージは`uv run --frozen`の後も残り、`uv sync --frozen`で除かれた

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
