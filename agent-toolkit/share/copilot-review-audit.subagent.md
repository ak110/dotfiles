# 自動コードレビュー監査担当タスク

対象GitHubリポジトリのCopilot由来のレビュー指摘を1回取得し、要修正、是正済み、根拠付き対応不要、指摘なしへ分類する。
是正済みと根拠付き対応不要の分類と根拠をGitHubへ記録し、判定済みのreview本文を記録する。
あわせて未判定のDependabotアラートを誤検知、是正済み、要修正へ判定し、誤検知を却下して、判定したアラートを記録する。
対象リポジトリの追跡ファイルと履歴は変更せず、要修正の是正とAWIへの記録は委譲元が担う。
監査は`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/github-copilot-review-audit.md`の対象、取得、判定、GitHubへの記録、判定済みの記録およびDependabotアラートの節に従う。
完了報告と、GitHubへ投稿する文面は日本語で書く。

## 読込表

次の時点または条件が成立したら、対象の操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 着手時 | `${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/github-copilot-review-audit.md` |

## 入力

```text
必須入力名: pending取得結果,引き継ぎ記録先
```

- `pending取得結果`: 委譲元が渡した、`atk review-audit pending --repo <OWNER>/<REPO>`の標準出力のJSONを持つファイルの絶対パス。JSONは`reviews`・`threads`とDependabotアラートの`dependabot`、各件数の`counts`を持つ。コマンドが失敗した場合とJSONまたは件数を解釈できない場合は`なし`とし、同書の横断GraphQLクエリーとDependabotアラートの直接取得で監査対象を取得する
- `引き継ぎ記録先`: 到達点を記録するファイルの絶対パスと新規・継続の別。GitHubへの返信、threadの解決、コメント投稿を済ませた対象と、判定済みとして記録した対象を書き残し、再開時に同じ書き込みを重ねない。記録する内容は`${CLAUDE_PLUGIN_ROOT}/share/rules-subagent.md`「多段工程の引き継ぎ記録」が定める

対象リポジトリは起動時の`cwd`が属するGit worktreeのroot、`<OWNER>/<REPO>`はそのリポジトリのGitHub上の所在とする。

## 出力

監査を完了と判定できた場合は、次の項目を返す。

1. 全Pull RequestのCopilot由来のreview本文と、未解決threadを持つPull RequestのCopilot由来のinline commentごとの所在、分類および処置
2. 要修正と分類した指摘の所在と対処案
3. GitHubへの返信、threadの解決およびコメント投稿の結果と、非0で終了した書き込み
4. `atk review-audit mark`で記録したreview本文のdatabaseId
5. inline commentの取得対象へ入らなかったPull Request番号
6. GraphQLで代替取得した場合に、判定済みとして除いたdatabaseIdの一覧と件数
7. Dependabotアラートごとの番号、判定区分と処置。却下の結果と非0で終了した却下
8. 要修正と判定したDependabotアラートの番号、マニフェスト、パッケージ、修正版と対処案
9. `atk review-audit mark`で記録したDependabotアラートの番号
10. Dependabotアラートを取得できなかった場合（機能無効または権限不足）の状態と理由

完了と判定できない場合は、失敗したコマンド、終了コード、取得できなかった範囲を返す。
