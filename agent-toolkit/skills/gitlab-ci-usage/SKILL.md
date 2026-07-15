---
name: gitlab-ci-usage
description: >
  GitLab CI（`.gitlab-ci.yml`）のキーワード仕様・典型パターン・lintのリファレンス。
---

# GitLab CIの使い方

`.gitlab-ci.yml`のキーワード仕様は改訂頻度が高く、訓練データだけでは最新のサブキーや非推奨化を網羅できない。

## 基本方針

キーワード仕様の確認は公式ドキュメントを直接WebFetchする。
訓練データ由来の記憶で書かず、必ず該当キーワードのページを取得してから構文を決める。
代表的な導線と典型パターンのみを以下に示す。網羅的な仕様は公式ドキュメントを参照する。

## テーマ別参照URL

テーマ別の代表ページを以下に示す。

- [キーワード全リファレンス](https://docs.gitlab.com/ci/yaml/): 未知のキーワード、サブキーの網羅確認
- [`rules` / `only` / `except`](https://docs.gitlab.com/ci/yaml/#rules):
  ジョブ起動条件、`rules:if` / `rules:changes` / `rules:exists`
- [`workflow:rules`](https://docs.gitlab.com/ci/yaml/workflow/):
  パイプライン自体の起動制御、`workflow:auto_cancel`
- [`include`](https://docs.gitlab.com/ci/yaml/includes/):
  `include:local` / `include:project` / `include:template` / `include:component`
- [`artifacts:reports`](https://docs.gitlab.com/ci/yaml/artifacts_reports/):
  `junit` / `coverage_report` / `dotenv` / `sast`などレポート種別
- [事前定義変数](https://docs.gitlab.com/ci/variables/predefined_variables/): `CI_*`変数の正確な名称と値のタイミング
- [CI Lint API](https://docs.gitlab.com/api/lint/): 外部からのlint呼び出し仕様
- [CI/CD components](https://docs.gitlab.com/ci/components/): コンポーネント定義・入力パラメーター

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
    - when: never  # 上記いずれにも該当しないケースは実行しない
```

暗黙のフォールスルー挙動に頼ると意図と異なる起動をしやすいため、末尾の`when: never`で明示する。

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

### `parallel:matrix`

```yaml
test:
  parallel:
    matrix:
      - PYTHON: ["3.11", "3.12", "3.13"]
        OS: ["ubuntu", "alpine"]
  script: ./test.sh
```

### `rules:changes`とスケジュール実行

`rules:changes`はGit pushイベントを伴わないパイプラインでは常にtrueと評価される。
対象は`$CI_PIPELINE_SOURCE`が`schedule`・`tag`・`pipeline`・`web`・`api`・`trigger`の場合で、
差分判定が成立しないため`changes`条件を通過する。

`schedule`等を導入する際は、対象外としたいジョブを次のいずれかで限定する。

対象外ジョブの`rules`先頭で`schedule`を除外する形式:

```yaml
job:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "schedule"'
      when: never
    - changes: [src/**/*]
```

`schedule`で起動したいジョブのみ`if`で起動条件を限定する形式:

```yaml
scheduled-check:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "schedule"'
  script: ./check.sh
```

## lint / 検証

`.gitlab-ci.yml`の妥当性検証には以下の手段がある。
ローカルで完結できる場合はまずローカルで確認し、最終確認でGitLab本体のlintを使う。

- [`gitlab-ci-local`](https://github.com/firecow/gitlab-ci-local):
  ローカルでジョブをシミュレート実行できるNode製CLI。構文チェックに加え、rulesの評価結果まで確認したい場合に使用
- `/api/v4/ci/lint`: GitLab本体のCI Lint API（`content`フィールドにyaml全文を渡す）。CI内やスクリプトからの自動検証
- プロジェクトの`/-/ci/lint`ページ: Web UIでの手動検証
  - `include`解決や変数込みの検証が可能
  - `include`先を含めた統合的な妥当性確認、最終確認に使用

GitLab本体のlintは`include`や`workflow`の評価まで実行するため、
ローカルの構文チェックだけでは検知できない統合レベルの誤りを検出できる。

## トラブルシューティング指針

CIが意図通りに動作しない場合は、原因の階層（起動条件・rules評価・依存関係・artifacts）を切り分けてから
[GitLab CI/CDドキュメント](https://docs.gitlab.com/ci/)の該当セクションを参照する。

## 私設ホスト（自己署名のTLS証明書）でのCI通過確認

自己署名のTLS証明書のGitLab私設ホストでは、`glab`が既定でTLS証明書検証エラー（`tls: failed to verify certificate`）で
動作しない場合がある。以下のいずれかで解消する。

- `glab config set skip_tls_verify true --host <host>`でホスト単位のTLS検証をスキップする
- 環境変数`GITLAB_HOST=<host>`と`GITLAB_TOKEN=<token>`を併せて設定する
- `glab`が全く機能しない場合の代替として`curl -k`によるAPI直呼び出しを使う

```text
curl -k -H "PRIVATE-TOKEN: <token>" \
  "https://<host>/api/v4/projects/<project-id>/pipelines?sha=<commit-sha>"
```

pipeline一覧からstatus=successを検知するまでpollingする。全て不可能な場合は
ユーザーの明示判断でCI通過確認スキップを許容する（記録は必須）。

`curl -k`はTLS検証をスキップするためMITM耐性が下がる。認証トークン漏洩防止のため、
トークンは環境変数経由で渡し、コマンド履歴に残さない運用とする。
