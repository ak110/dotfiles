---
name: gitlab-ci-usage
description: >
  GitLab CI設定のキーワード仕様・典型パターン・lint実行方法を参照するときに起動する。
  `.gitlab-ci.yml`の編集・確認時に起動する。
---

# GitLab CIの使い方

本スキルは、GitLab CI設定に関する知識を提供する。
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

## 誤りやすい点と推奨

基礎構文（`rules:if`・`needs`・`extends`・`parallel:matrix`等）は公式ドキュメントを参照する。
以下は誤りやすい点だけを示す。

- `rules`の暗黙のフォールスルー挙動に頼ると意図と異なる起動をしやすいため、末尾の`when: never`で明示する
- `include`の`ref`はタグまたはコミットSHA固定を推奨する。ブランチ名参照は意図せず挙動が変わるため避ける

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

自己署名のTLS証明書のGitLab私設ホストで`glab`がTLS証明書検証エラーになる場合の対処は、
`references/self-hosted-tls.md`を読む。
