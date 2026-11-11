# 監査記録: プロジェクトの検証

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## .claude/skills/develop-dotfiles/references/verification-values.md：変更範囲の検証の値：2026年10月4日

pyfltr 3.20.0の`pytest-fast-targets`へ`*_invariant_test.py`を指定した。
指定した時点では横断テストをpytestのマーカー`repo_invariant`でも識別しており、マーカーでの収集と専用ファイルだけの収集は、ファイル名を除いた各nodeの多重集合が一致し、両側とも130件だった。
クラス内の字下げされたマーカー1件も比較で検出して分離した。
その後、fastがマーカーを選択に使わないため、マーカーを撤去してファイル名だけで識別する形にした。
撤去後の確認では、fastのpytestが受け取ったファイルは33件で、`git ls-files`が返す`*_invariant_test.py`の33件と一致した。

再検証はrootで次を個別に実行する。

```sh
git ls-files -- '*_invariant_test.py'
uv run --frozen pyfltr fast --commands=pytest
uv run --frozen pyfltr fast --commands=pytest --pytest-args='--collect-only -q' --output-format=jsonl
uv run --frozen pyfltr show-run <run_id> --commands pytest --output --output-format=json
uv run --frozen prek run pyfltr --files docs/development/design-packages.md
```

3行目の出力の1行目（`"kind":"header"`）が示す`run_id`を4行目へ渡す。
4行目が返すJSONの`output`に、pyfltrのsubprojectごとの`# subproject: <ディレクトリ>`の行と、収集したテストの`<ファイル>::<テスト名>`の行が並ぶ。
各行のファイルへsubprojectのディレクトリ（`.`はroot）を前置した集合が、fastのpytestが受け取ったファイルの集合である。
この集合が`git ls-files`の一覧と一致し、通常の動作テストのファイルを含まないことを確認する。
件数だけを比べると、対象のファイルが別のファイルへ置き換わった場合を見逃すため、集合を比べる。
Markdownだけを渡したprekのhookからpytestが起動し、不変条件の失敗が同じhookの失敗へ届くことも確かめる。
rebaseで組合せが変わった場合は、commit時の成功だけで判定せず、同じfastのpytestを統合前に再実行する。

## .claude/skills/develop-dotfiles/references/verification-values.md：変更範囲の検証の値：2026年10月18日

Python 3.15、CIと同じimageのdigest `sha256:b13645f08610527422a741872c2457ee7b0e2ce96880acee22be2f1984477600`で、lockだけの変更によるsdistビルドの差を観測した。
基準commit `a151f22a2a3447cfc4925e2a708d279138de46f4`のarchiveへ、`uv.lock`のzstandard依存2行から`python_full_version < '3.14'`のmarkerだけを除いた入力を作成した。
両入力を同じcontainerの`poppler-utils libarchive13 zstd`導入後に`UV_FROZEN=1 UV_PYTHON=3.15 uv sync --all-groups --all-extras`へ渡した。
marker保持側は終了0、除去側はzstandard 0.25.0のビルドに到達して`FileNotFoundError: No such file or directory: 'cc'`で終了1となった。
現行commit `3c7276065cc081b9759f2702b7d149d3aaa06c7c`のlockにはzstandardが含まれないため、故障対照は基準版の入力として保持する。
同じ現行commitのarchiveも上記と同じ操作へ渡し、135パッケージの依存導入と終了0を確認した。

再検証では同じ基準版のarchiveから上記2入力を作成し、同じimage・ネイティブパッケージ・Python版・uvの同期引数を使う。
実行結果は基準版の正常導入とlock変更によるビルド失敗を区別して読む。
`ci_workflow_test.py::test_update_checks_container_dependencies_after_lock_updates`で依存更新の手順を確かめる。
`update`から`$(MAKE) ci-deps-check`を除いたMakefileでは、同じ試験が失敗することを確かめる。
`make -n update`では両lockの更新後に`ci-deps-check`の`docker run`が出力された。

## .claude/skills/develop-dotfiles/references/verification-values.md：変更範囲の検証の値：2026年11月9日

pyfltr 3.27.0の`uv run --frozen pyfltr config list --all --output-format json`で、`mypy-fast`・`pylint-fast`・`pyright-fast`がfalse、`ruff-check-fast`がtrueであることを確認した。
実効値の取得は設定だけを照会し、各ツールを実行した観測とは区別する。
再検証では同じコマンドの`values`にある各キーの`value`を読み、計画のツール集合と比較する。
実行済みの集合・対象・終了状態は`pyfltr list-runs`と`pyfltr show-run <run_id>`から個別に確認する。
fastから不足するツールがあれば、その成功だけで計画のlint全体を置き換えない。

## develop-dotfiles：Git動作テストの状態準備：2026年10月9日

出所はリポジトリ外に保存した対照結果と、導入commit `b20e6f20514a381648c730bbf828e2f8ac9e3522`、修正commit `f071d6348670a39e797e9dc54f6276e1e9dad8f6`の実装である。
対象は`agent-toolkit/agent_toolkit/_hooks/pretooluse/shell_checks_test.py`の`TestBashGitRevParseShortMultiple.test_individual_revision_fix_succeeds`。
入力は`git rev-parse`のoption `--short`、`--short=12`、`--verify`、`--verify --quiet`とrevision `HEAD`、`HEAD~1`の8組である。

| 履歴条件 | HEADの4入力 | HEAD~1の4入力 |
| --- | --- | --- |
| 通常履歴 | 全件終了0 | 全件終了0 |
| HEADを浅い境界とする深さ1相当 | 全件終了0 | optionの上記順に128・128・128・1 |

再検証は一時repoに2commitを作成し、通常履歴と同じHEADを浅い境界とするrepoで上記8入力をそれぞれ実行する。
対象repoを明示し、入力・履歴条件・終了コード・両出力を保存する。
修正後のテストは`init_repository`と`commit_all`で必要な2commitを用意し、必須repo引数を`git -C`へ渡す`run_git`で検証する。
`_testing/isolation.py:isolate_development_state`は設定と環境を隔離するが、cwdの変更やcommit生成は行わない。
この違いを設定隔離の成功だけから履歴準備の成立へ読み替えない。
CI run `37887505483`の失敗と`37889681072`の成功はリポジトリ外の確認記録に由来し、本記録の作成時にCIは再照会していない。
