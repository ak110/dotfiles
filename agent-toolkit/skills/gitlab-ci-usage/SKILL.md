---
name: gitlab-ci-usage
user-invocable: false
description: >
  GitLab CI設定のキーワード仕様・典型パターン・lint実行方法を参照するときに起動する。
  `.gitlab-ci.yml`の編集・確認時に起動する。
---

# GitLab CIの使い方

本スキルはGitLab CI設定に関する知識を提供する。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 自己署名のTLS証明書のGitLab私設ホストで`glab`がTLS証明書検証エラーを返したとき | `references/self-hosted-tls.md` |
| 私設GitLabのCI通過確認を行う前 | `agent-toolkit:commit`の`references/push-and-ci.md` |

## 基本方針

新規に使うキーワード、改訂の多いキーワード、不確かな構文は、対象バージョンの公式ドキュメントをWebFetchで取得してから採用する。
既知の基礎構文は取得費用と確認の必要性を比べ、非推奨の仕様を記憶だけで採用することを避ける。

## テーマ別参照URL

[キーワード全リファレンス](https://docs.gitlab.com/ci/yaml/)を起点に、`rules`、`workflow`、`include`、`artifacts:reports`、componentsなど対象の仕様へ進む。
変数の名称と評価時点は[事前定義変数](https://docs.gitlab.com/ci/variables/predefined_variables/)、外部からの検証は[CI Lint API](https://docs.gitlab.com/api/lint/)で確認する。

## 誤りやすい点と推奨

起動条件を省略した場合の動作、参照先の版、対象インスタンスでの利用可否を確認し、意図したジョブだけが再現可能な設定で動くようにする。
`rules`のフォールスルーや`include`の`ref`など、暗黙値と変動する参照を点検する。
機能ページの`Tier`・`Offering`と、YAMLで設定できる属性かAPI専用かを区別する。
editionは対象ホストを同じ読込文脈から特定できる場合に`glab api version`の`enterprise`で判定する（`false`はCommunity Edition）。
ホストが未確定なら、tierに依存する機能の採否は確定後に判断する。

### `rules:changes`とスケジュール実行

`rules:changes`はGit pushイベントを伴わないパイプラインでは常にtrueと評価される。
対象は`$CI_PIPELINE_SOURCE`が`schedule`・`tag`・`pipeline`・`web`・`api`・`trigger`の場合で、
差分判定が成立しないため`changes`条件を通過する。

`schedule`等を導入する際は、対象外としたいジョブの`rules`先頭で`schedule`を除外する。

```yaml
job:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "schedule"'
      when: never
    - changes: [src/**/*]
```

## lint / 検証

ローカルで完結する検証は[`gitlab-ci-local`](https://github.com/firecow/gitlab-ci-local)などで先に確認する。
最終確認は`include`・`workflow`・変数を統合評価するGitLab本体のlintを使う。
自動検証は`/api/v4/ci/lint`の`content`へYAML全文を渡し、手動検証はプロジェクトの`/-/ci/lint`ページを使う。
私設ホストのCI通過確認とTLSエラーの扱いは読込表に従う。
