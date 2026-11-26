---
mode: delegate
model_type: execute
required_inputs:
- pending取得結果
- 引き継ぎ記録先
optional_inputs: []
writes_worktree: false
startup_documents:
- agent-toolkit:atk-mode/playbooks/process-wi-review-audit.md
---

# 自動コードレビュー監査担当タスク

自動コードレビュー監査担当が、Copilotの指摘とDependabotのアラートを漏れなく判定して記録し、修正が要る項目をユーザーの成果へ確実に反映させるための文書である。
対象のGitHubリポジトリで、Copilot由来のレビュー指摘と未判定のDependabotアラートを1回取得して監査し、分類、GitHubへの記録、判定済みの記録までを終える。
対象リポジトリの追跡対象ファイルとcommit履歴は変えない。
要修正と判定した項目の是正とAWIへの記録は委譲元が行う。
手順は`agent-toolkit:atk-mode/playbooks/process-wi-review-audit.md`（起動時に届く）と`agent-toolkit:atk-mode/playbooks/process-wi-dependabot-audit.md`にある。

## 入力

- `pending取得結果`: `atk review-audit pending --repo <OWNER>/<REPO>`の標準出力を保存したJSONファイルの絶対パス。
  `なし`の場合は、`agent-toolkit:atk-mode/playbooks/process-wi-review-audit.md`「取得」の代替取得で監査対象を取得する。
- `引き継ぎ記録先`: 到達点を記録するファイルの絶対パスと、新規か継続かの区分。
  GitHubへの返信、threadの解決、コメントの投稿、判定済みの記録を終えた対象を書き残し、再開した時に同じ書き込みを重ねないようにする。

対象リポジトリは起動時の`cwd`が属するGit作業ツリーのルートとし、`<OWNER>/<REPO>`はそのリポジトリのGitHub上の識別子とする。

## 手順

1. 起動時に届いた`agent-toolkit:atk-mode/playbooks/process-wi-review-audit.md`と`pending取得結果`を読み、監査の入力を確定する。
   `なし`なら同書「取得」の代替取得を使う。
2. Copilotのレビューは、同書「対象」から「判定済みの記録」までを行う。
3. Dependabotアラートは、同書「判定済みの記録」の手順4から`agent-toolkit:atk-mode/playbooks/process-wi-dependabot-audit.md`へ進む。
   判定の記録は版付きで行う。
   取得・比較・markには同じ確認SHA（通常取得は`dependabot.revision`、代替取得は`default_branch`が指すブランチで固定した完全SHA）を使う。
   取得に成功した0件は判定対象なしとし、機能無効・権限不足と区別して取得状態を返す。
4. 却下とmarkの条件は`agent-toolkit:atk-mode/playbooks/process-wi-dependabot-audit.md`「却下」「判定したアラートの記録」に従う。
   却下はAPIの終了コード0、応答の`number`の一致と`state=dismissed`を確かめた番号だけを処置完了とし、却下の失敗と未判定はmarkせずに返す。

## 出力

取得状態と処置結果を確かめ、監査が完了した場合は次の形式のみを返す。

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

続行不能と追加報告行は、共通契約`agent-toolkit:atk-mode/playbooks/subagent.md`「返却形式の受け渡し」に従う。
取得不能の理由には、失敗したコマンド、終了コードと取得できなかった範囲を記す。
