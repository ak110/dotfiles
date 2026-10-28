---
name: gitlab-ci-usage
user-invocable: false
description: >
  GitLab CI設定のキーワード仕様・典型パターン・lint実行方法を参照するときに起動する。
  `.gitlab-ci.yml`の編集・確認時に起動する。
---

# GitLab CIの使い方

設定を検討する際は、対象バージョンの公式資料とYAMLの読み取りが必要であり、最終確認には対象GitLabのlintを実行できる手段が必要となる。GitLabやCLIに接続できない環境では、提示された設定の検討結果と本体lintへ渡す入力を返し、構文通過の判定はGitLabへ接続できるClaude CodeまたはCodexで確認する。読み手へ説明を伝える際は、[文章の品質基準](references/quality-standards.md)を用いる。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/gitlab-ci-usage/references/quality-standards.md` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 自己署名のTLS証明書のGitLab私設ホストで`glab`がTLS証明書検証エラーを返したとき | `references/self-hosted-tls.md` | 判断 | `read:agent-toolkit:*/skills/gitlab-ci-usage/references/self-hosted-tls.md` | |
| 私設GitLabのCI通過確認を行う前 | `agent-toolkit:commit`の`references/push-and-ci.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/push-and-ci.md` | |

## 基本方針

新しく使うキーワードや改訂頻度の高いキーワード、構文が不確かな項目は、記憶に頼らず対象バージョンの公式ドキュメントで確認してから採用する。
参照の起点には[キーワード全リファレンス](https://docs.gitlab.com/ci/yaml/)、変数の確認には[事前定義変数](https://docs.gitlab.com/ci/variables/predefined_variables/)、外部からの構文検証には[CI Lint API](https://docs.gitlab.com/api/lint/)を用いる。
機能説明ページの`Tier`や`Offering`を確認し、YAMLで設定できる項目なのかAPI専用なのかを区別する。エディションは、対象ホストが確定している場合に`glab api version`の`enterprise`の値で判定する（`false`の場合はCommunity Editionを表す）。

## `rules:changes`とスケジュール実行

`compare_to`を指定しない`rules:changes`は、新規ブランチでの実行時や、タグ・スケジュール・手動実行のように比較対象となるGit pushイベントを伴わないパイプラインにおいて、常にtrueと評価される（[公式仕様](https://docs.gitlab.com/ci/yaml/#ruleschanges)）。
差分比較したい場合は`rules:changes:compare_to`で比較元を明示し、実行対象から外したいジョブは`rules`の先頭で除外する。
なお、タグpush時の`CI_PIPELINE_SOURCE`は`push`となるため、タグの判定には`CI_COMMIT_TAG`を使用する（[パイプライン種別の判定](https://docs.gitlab.com/ci/jobs/job_rules/#ci_pipeline_source-predefined-variable)）。

```yaml
job:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "schedule"'
      when: never
    - changes: [src/**/*]
```

## lint / 検証

ローカル環境で完結する検証は、まず[`gitlab-ci-local`](https://github.com/firecow/gitlab-ci-local)などを活用して確認する。最終確認は、`include`や`workflow`、変数をまとめて評価できるGitLab本体のlint機能（`/api/v4/ci/lint`の`content`にYAML全文を渡すか、Web UIの`/-/ci/lint`ページ）で行う。
