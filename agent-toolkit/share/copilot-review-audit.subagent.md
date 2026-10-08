# 自動コードレビュー監査担当タスク

対象GitHubリポジトリのCopilot由来のレビュー指摘と未判定のDependabotアラートを1回取得して監査し、分類、GitHubへの記録および判定済みの記録までを行う。
対象リポジトリの追跡ファイルと履歴は変更せず、要修正の是正とAWIへの記録は委譲元が担う。
最初に読込表の着手時の資料を読み、受領した`pending取得結果`のJSONを読む。分類と判定の区分、取得、GitHubへの記録（誤検知のアラートの却下を含む）と判定済みの記録は、同書の対象、取得、判定、GitHubへの記録、判定済みの記録、Dependabotアラートの各節に従う。
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

- `pending取得結果`: 委譲元が渡した、`atk review-audit pending --repo <OWNER>/<REPO>`の標準出力のJSONを持つファイルの絶対パス。JSONの各キーの内容は同書の取得の節とDependabotアラートの節が定める。コマンドが失敗した場合とJSONまたは件数を解釈できない場合は`なし`とし、同じ2節の代替取得の手順で監査対象を取得する
- `引き継ぎ記録先`: 到達点を記録するファイルの絶対パスと新規・継続の別。GitHubへの返信、threadの解決、コメント投稿を済ませた対象と、判定済みとして記録した対象を書き残し、再開時に同じ書き込みを重ねない。記録する内容は`${CLAUDE_PLUGIN_ROOT}/share/rules-subagent.md`「多段工程の引き継ぎ記録」が定める

対象リポジトリは起動時の`cwd`が属するGit worktreeのroot、`<OWNER>/<REPO>`はそのリポジトリのGitHub上の所在とする。

## 出力

監査を完了と判定できた場合は、次の形式だけを返す。

```text
状態: completed
指摘の分類: <全Pull RequestのCopilot由来のreview本文と、未解決threadを持つPull RequestのCopilot由来のinline commentごとの所在、分類および処置>
要修正の指摘: <要修正と分類した指摘の所在と対処案。無い場合は「なし」>
GitHubへの書き込み: <返信、threadの解決およびコメント投稿の結果と、非0で終了した書き込み。無い場合は「なし」>
記録したreview本文: <`atk review-audit mark`で記録したreview本文のdatabaseId。無い場合は「なし」>
取得対象外のPull Request: <inline commentの取得対象へ入らなかったPull Request番号。無い場合は「なし」>
代替取得で除いた判定済み: <GraphQLで代替取得した場合に、判定済みとして除いたdatabaseIdの一覧と件数。代替取得しなかった場合は「なし」>
Dependabotアラートの判定: <アラートごとの番号、判定区分と処置。却下の結果と非0で終了した却下を含む。無い場合は「なし」>
要修正のDependabotアラート: <番号、マニフェスト、パッケージ、修正版と対処案。無い場合は「なし」>
記録したDependabotアラート: <`atk review-audit mark`で記録した番号。無い場合は「なし」>
Dependabotアラートの取得状態: <取得できた場合は「取得済み」。機能無効または権限不足で取得できなかった場合はその状態と理由>
```

完了と判定できない場合は1行目の`状態`行を置かず、`続行できない理由: <失敗したコマンド、終了コード、取得できなかった範囲>`の行を末尾へ置く。この形式は`agent-toolkit/share/rules-subagent.md`「返却形式の受け渡し」の規則に従う。
