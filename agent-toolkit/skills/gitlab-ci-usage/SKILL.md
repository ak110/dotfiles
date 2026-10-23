---
name: gitlab-ci-usage
user-invocable: false
description: >
  GitLab CI設定のキーワード仕様・典型パターン・lint実行方法を参照するときに起動する。
  `.gitlab-ci.yml`の編集・確認時に起動する。
---

# GitLab CIの使い方

設定の検討には対象版の公式資料とYAMLの読取、最終確認には対象GitLabのlintへ到達する手段が必要である。GitLabやCLIへ到達できない環境では、提示された設定の検討結果と本体lintへ渡す入力を返し、通過判定はGitLabへ接続できるClaude CodeまたはCodexで確かめる。読み手へ伝える説明には[文章の品質基準](references/quality-standards.md)を使う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/gitlab-ci-usage/references/quality-standards.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 自己署名のTLS証明書のGitLab私設ホストで`glab`がTLS証明書検証エラーを返したとき | `references/self-hosted-tls.md` | 判断 | `read:agent-toolkit:*/skills/gitlab-ci-usage/references/self-hosted-tls.md` | |
| 私設GitLabのCI通過確認を行う前 | `agent-toolkit:commit`の`references/push-and-ci.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/push-and-ci.md` | |

## 基本方針

新規に使うキーワード、改訂の多いキーワード、不確かな構文は、記憶に頼らず対象バージョンの公式ドキュメントで確かめてから採用する。
起点は[キーワード全リファレンス](https://docs.gitlab.com/ci/yaml/)、変数は[事前定義変数](https://docs.gitlab.com/ci/variables/predefined_variables/)、外部からの検証は[CI Lint API](https://docs.gitlab.com/api/lint/)とする。
機能ページの`Tier`・`Offering`を確かめ、YAMLで設定できる属性かAPI専用かを区別する。editionは対象ホストが確定している場合に`glab api version`の`enterprise`で判定する（`false`はCommunity Edition）。

## `rules:changes`とスケジュール実行

`compare_to`を指定しない`rules:changes`は、新規ブランチと、タグ・スケジュール・手動実行など比較元のGit pushイベントを伴わないパイプラインでは常にtrueと評価される（[公式仕様](https://docs.gitlab.com/ci/yaml/#ruleschanges)）。
差分比較が必要なら`rules:changes:compare_to`で比較元を指定し、対象外としたいジョブは`rules`先頭で除外する。
タグpushの`CI_PIPELINE_SOURCE`は`push`であり、タグの判定には`CI_COMMIT_TAG`を使う（[パイプライン種別の判定](https://docs.gitlab.com/ci/jobs/job_rules/#ci_pipeline_source-predefined-variable)）。

```yaml
job:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "schedule"'
      when: never
    - changes: [src/**/*]
```

## lint / 検証

ローカルで完結する検証は[`gitlab-ci-local`](https://github.com/firecow/gitlab-ci-local)などで先に確認し、最終確認は`include`・`workflow`・変数を統合評価するGitLab本体のlint（`/api/v4/ci/lint`の`content`へYAML全文、または`/-/ci/lint`ページ）で行う。
