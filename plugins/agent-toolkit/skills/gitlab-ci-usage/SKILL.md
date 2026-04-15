---
name: gitlab-ci-usage
description: GitLab CI/CD（`.gitlab-ci.yml`）編集時のキーワード仕様・典型パターン・lint手段・トラブルシューティング観点の参照リファレンス。`.gitlab-ci.yml`を新規作成または修正するとき、`rules`・`workflow`・`needs`・`include`・`extends`・`artifacts`・`cache`などキーワードの仕様やサブキーを確認したいとき、ジョブの起動条件や依存関係の書き方で迷ったとき、CIパイプラインが意図通りに動かず原因を切り分けたいとき、設定ファイルをlint/validateしたいときに使う。`.gitlab-ci.yml`が存在するリポジトリや、GitLab CIをセットアップしようとしているプロジェクトで特に有用。
user-invocable: true
---

# GitLab CIの使い方

`.gitlab-ci.yml`のキーワード仕様は改訂頻度が高く、訓練データだけでは最新のサブキーや非推奨化を追いきれない。
本スキルは公式ドキュメントへのピンポイントWebFetchを一次情報源として、編集時の参照・確認を効率化するためのリファレンスを集約する。

## 基本方針

キーワード仕様の確認は公式ドキュメントを直接WebFetchする。
訓練データ由来の記憶で書かず、必ず該当キーワードのページを取得してから構文を決める。
本スキルに書かれているのは代表的な導線と典型パターンのみであり、網羅的な仕様は公式側に委ねる。

## 参照先URL

テーマ別の代表ページを以下に示す。
該当項目をWebFetchしてから編集することで、サブキーの取り得る値や非推奨化を見落とさずに済む。

| テーマ | URL | 使う場面 |
| -- | -- | -- |
| キーワード全リファレンス | <https://docs.gitlab.com/ci/yaml/> | 未知のキーワード、サブキーの網羅確認 |
| `rules` / `only` / `except` | <https://docs.gitlab.com/ci/yaml/#rules> | ジョブ起動条件、`rules:if` / `rules:changes` / `rules:exists` |
| `workflow:rules` | <https://docs.gitlab.com/ci/yaml/workflow/> | パイプライン自体の起動制御、`workflow:auto_cancel` |
| `include` | <https://docs.gitlab.com/ci/yaml/includes/> | `include:local` / `include:project` / `include:template` / `include:component` |
| `artifacts:reports` | <https://docs.gitlab.com/ci/yaml/artifacts_reports/> | `junit` / `coverage_report` / `dotenv` / `sast`などレポート種別 |
| 事前定義変数 | <https://docs.gitlab.com/ci/variables/predefined_variables/> | `CI_*`変数の正確な名称と値のタイミング |
| CI Lint API | <https://docs.gitlab.com/api/lint/> | 外部からのlint呼び出し仕様 |
| CI/CD components | <https://docs.gitlab.com/ci/components/> | コンポーネント定義・入力パラメーター |

## 典型パターン

日常的に頻出する構文の最小スニペットを以下に示す。
個別のサブキーや組み合わせの妥当性は公式ドキュメントと照合する。

### `rules:if`による条件分岐

```yaml
job:
  script: ./build.sh
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
      when: always
    - when: never
```

末尾の`when: never`で「上記以外は実行しない」を明示する。
暗黙のフォールスルー挙動に頼ると意図と異なる起動をしやすい。

### `needs`によるDAG構築

```yaml
build:
  stage: build
  script: ./build.sh
test:
  stage: test
  needs: [build]
  script: ./test.sh
deploy:
  stage: deploy
  needs:
    - job: test
    - job: build
      artifacts: true
  script: ./deploy.sh
```

`needs`を使うとstage順に縛られず依存順で先行実行できる。
`artifacts: true`は既定値だが、明示すると意図が伝わりやすい。

### `include`による設定分割

```yaml
include:
  - local: .gitlab/ci/lint.yml
  - project: my-group/ci-templates
    ref: v1.2.0
    file: /templates/python.yml
  - component: $CI_SERVER_FQDN/my-group/component-name/job@1.0
    inputs:
      stage: test
```

`ref`はタグまたはコミットSHA固定を推奨する。
ブランチ名参照は意図せず挙動が変わるため避ける。

### `extends`によるジョブテンプレート再利用

```yaml
.base:
  image: python:3.12
  before_script:
    - uv sync

test:
  extends: .base
  script: uv run pytest
```

先頭に`.`を付けたジョブはhidden jobとなり単体実行されない。
`extends`は配列で複数指定もでき、後方優先で上書きされる。

### `parallel:matrix`

```yaml
test:
  parallel:
    matrix:
      - PYTHON: ["3.11", "3.12", "3.13"]
        OS: ["ubuntu", "alpine"]
  script: ./test.sh
```

組み合わせの各セルが独立ジョブとして展開される。
ジョブ名には変数値が付与されるため、`needs`で指すときは展開後の名前を意識する。

## lint / 検証

`.gitlab-ci.yml`の妥当性検証には以下の手段がある。
ローカル完結で可能ならまずローカルで確認し、最終確認でGitLab本体のlintを使うのが無駄が少ない。

| 手段 | 特徴 | 使い所 |
| -- | -- | -- |
| `gitlab-ci-local` | ローカルでジョブをシミュレート実行できるNode製CLI（<https://github.com/firecow/gitlab-ci-local>） | 構文チェックに加え、rulesの評価結果まで確認したいとき |
| `/api/v4/ci/lint` | GitLab本体のCI Lint API（`content`フィールドにyaml全文を渡す） | CI内やスクリプトからの自動検証 |
| プロジェクトの`/-/ci/lint`ページ | Web UIでの手動検証。`include`解決や変数込みの検証が可能 | 最終確認、`include`先を含めた統合的な妥当性確認 |

GitLab本体のlintは`include`や`workflow`評価までを行うため、ローカルの構文チェックだけでは検知できない統合レベルの誤りを拾える。

## トラブルシューティング指針

CIが意図通りに動かないときは、原因の階層を切り分けてから該当するドキュメントに当たる。

- パイプライン自体が起動しない
  - `workflow:rules`で除外されていないか確認する
  - ブランチ保護やMerge Request設定で起動条件が制限されていないか確認する
  - `.gitlab-ci.yml`のYAML構文エラーでない場合、CI/CD設定ページのパイプライン最終状態を参照する
- 特定ジョブだけスキップされる
  - ジョブの`rules`全条件を追う。最後に`when: never`が付いていないか確認する
  - `only`/`except`と`rules`を同時指定していないか確認する（併用不可）
  - `needs`で指定した先行ジョブが失敗・スキップしている場合、依存側も起動しない
- `needs`サイクルエラー
  - `needs`は有向非循環グラフでなければならない。ジョブ間依存を図示して閉路を洗い出す
- `extends`展開が期待と異なる
  - マージ順は「テンプレート → extends元」で後勝ち
  - `before_script`などはマージではなく上書きされるため、共通化したい場合は`default`セクションまたは`!reference`タグを検討する
- `include`の解決失敗
  - `project`参照は`ref`とファイルパスの両方が必要。`ref`が存在しないと解決失敗する
  - `component`参照はバージョン（`@`以降）の指定が必須
- `artifacts`が後続ジョブで見つからない
  - `artifacts:paths`のマッチ対象を確認する。ジョブ作業ディレクトリからの相対パスである
  - `needs:artifacts: false`になっていないか確認する
  - `artifacts:expire_in`で期限切れしていないか確認する
