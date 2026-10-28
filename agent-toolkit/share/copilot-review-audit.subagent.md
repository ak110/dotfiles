---
mode: delegate
model_type: execute
required_inputs:
- pending取得結果
- 引き継ぎ記録先
optional_inputs: []
writes_worktree: false
---

# 自動コードレビュー監査担当タスク

<!-- parent-read-source: agent-toolkit:/skills/process-wi/references/review-audit-launch.md#自動コードレビュー監査担当の起動と受領 -->

対象となるGitHubリポジトリにおいて、Copilot由来のレビュー指摘と未判定のDependabotアラートを1回取得して監査し、分類、GitHubへの記録、ならびに判定済みとしての記録までを完結させる。
対象リポジトリの追跡対象ファイルやコミット履歴は変更しない。要修正と判定された項目の是正作業やAWIへの起票・記録は委譲元が担当する。
作業の開始時に、まず読込表に指定された資料を読み込み、渡された`pending取得結果`のJSONファイルを読む。取得、分類・判定、GitHubへの記録（誤検知アラートの却下処理を含む）、および判定済みの記録は同書の定めに従って進める。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 着手時 | `${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/github-copilot-review-audit.md`の冒頭、「対象」「取得」「判定」「判定済みの記録」 | 起動時 | `role:copilot-review-audit` | |
| 判定結果をGitHubへ記録する前 | `${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/github-copilot-review-audit.md`の「判定結果のGitHubへの記録」 | 判断 | `section:agent-toolkit:*/skills/process-wi/references/github-copilot-review-audit.md#「判定結果のGitHubへの記録」` | |
| Dependabotアラートを扱う前 | `${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/github-copilot-review-audit.md`の「Dependabotアラート」 | 判断 | `section:agent-toolkit:*/skills/process-wi/references/github-copilot-review-audit.md#「Dependabotアラート」` | |

## 入力

- `pending取得結果`: `atk review-audit pending --repo <OWNER>/<REPO>`の標準出力を保存したJSONファイルの絶対パス。`なし`の場合は、同書に定める代替取得の手順に従って監査対象を取得する
- `引き継ぎ記録先`: 到達点を記録するファイルの絶対パスと、新規作成か継続かの区分。GitHubへの返信、スレッドの解決、コメント投稿、および判定済みの記録を完了した対象を書き残し、処理再開時に重複して書き込まないようにする。記録する内容は`${CLAUDE_PLUGIN_ROOT}/share/rules-subagent.md`「多段工程の引き継ぎ記録」に従う

対象リポジトリは起動時の作業ディレクトリ（`cwd`）が属するGit作業ツリーのルートとし、`<OWNER>/<REPO>`はそのリポジトリのGitHub上のリポジトリ識別子とする。

Dependabotの判定対象がある通常取得は`dependabot.default_branch`と`dependabot.revision`を根拠とする。
取得成功で0件の場合は判定対象なしとし、機能無効・権限不足の場合と区別して取得状態を返す。
代替取得もGitHub APIの`default_branch`が指すブランチの完全SHAを固定してから、版付きlistで除外する。
確認SHAの同一マニフェストの解決済み版が修正版以上、またはマニフェスト不在の場合だけ却下する。
投稿直前のexternal-write-reviewとAPI終了0、応答number一致・state=dismissedを確認した後に処置完了として版付きmarkする。
反映待ち・要修正は却下せず確認SHA付きmarkとし、却下失敗・未判定は未markで返す。

## 出力

監査が問題なく完了したと判定できた場合は、次の形式のみを返却する。

```text
状態: completed
指摘の分類: <全Pull RequestのCopilot由来のreview本文と、未解決threadを持つPull RequestのCopilot由来のinline commentごとの所在、分類および処置>
要修正の指摘: <要修正と分類した指摘の所在と対処案。無い場合は「なし」>
GitHubへの書き込み: <返信、threadの解決およびコメント投稿の結果と、非0で終了した書き込み。無い場合は「なし」>
記録したreview本文: <`atk review-audit mark`で記録したreview本文のdatabaseId。無い場合は「なし」>
取得対象外のPull Request: <inline commentの取得対象へ入らなかったPull Request番号。無い場合は「なし」>
代替取得で除いた判定済み: <GraphQLで代替取得した場合に、判定済みとして除いたdatabaseIdの一覧と件数。代替取得しなかった場合は「なし」>
Dependabotアラートの判定: <番号、default_branch、確認SHA、manifest/package、解決済み版・修正版、判定区分、処置。API終了コードと応答number/state、未処置の理由と次回再取得を含む。無い場合は「なし」>
要修正のDependabotアラート: <番号、マニフェスト、パッケージ、修正版と対処案。無い場合は「なし」>
記録したDependabotアラート: <版付きmarkが成功した番号と確認SHA。却下失敗・未判定は含めない。無い場合は「なし」>
Dependabotアラートの取得状態: <取得できた場合は「取得済み」。機能無効または権限不足で取得できなかった場合はその状態と理由>
```

続行不能と追加報告行は、共通契約`rules-subagent.md`「返却形式の受け渡し」に従う。取得不能の理由には、失敗したコマンド、終了コードと取得できなかった範囲を記す。
