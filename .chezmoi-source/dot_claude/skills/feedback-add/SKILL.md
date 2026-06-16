---
name: feedback-add
description: >
  フリーフォーマットのフィードバック本文を章構成に整形し、
  `feedback-add` CLI経由で~/private-notes/feedback/inbox/へ投入する。
# 連携: `feedback-add` CLI（pytools.feedback_inbox_add）に標準入力で投入する。
# フラグファイル ~/.config/agent-toolkit/feedback-inbox.enabled が存在する環境でのみ動作する。
---

# フィードバック投入

## 起動方針

`~/.config/agent-toolkit/feedback-inbox.enabled`が存在しない場合は、
フィードバック蓄積機能が無効である旨を1文示して終了する。

`~/private-notes`が存在しない場合は、手動で`~/private-notes`をクローンしてから
再度実行する旨を1文示して終了する。

## ステップ1: 入力の確認

ユーザーが提示したフィードバック本文を読み取る。
本文が提示されていない場合はユーザーに提供を求める。

## ステップ2: 章への仕分け

各項目の主題から対象を判別し、以下の3章へ仕分ける。

- `## pyfltr改善提案`: pyfltrリポジトリ（`~/pyfltr`）への提案
- `## agent-toolkit改善提案`: agent-toolkitプラグイン・配布ルール・dotfiles配布物への提案
- `## プロジェクトドキュメント改善提案`: 現在作業中のプロジェクトリポジトリへの提案

判別が困難な項目はユーザーへ確認する。
該当章に項目が無い場合、その章自体を出力に含めない。

## ステップ3: 項目の整形

各項目を「対象 — 提案内容」の形式（区切りは全角ダッシュ`—`）に整形する。
対象が特定ファイル・特定節の場合は対象欄に明示する。
対象が不明な項目は対象欄を空にし全角ダッシュなしで提案内容のみ記述する。

## ステップ4: 投入

整形済みmarkdownを`feedback-add` CLIへ標準入力で渡す。
プロジェクトドキュメント章を含む場合は`--project-doc-repo`にカレントディレクトリを渡す。

実行例（プロジェクトドキュメント章を含む場合）。

```sh
feedback-add --project-doc-repo "$(pwd)" <<'EOF'
## pyfltr改善提案

- <対象> — <提案内容>

## agent-toolkit改善提案

- <対象> — <提案内容>

## プロジェクトドキュメント改善提案

- <対象> — <提案内容>
EOF
```

## ステップ5: 結果の提示

`feedback-add` CLIの標準出力をそのままユーザーへ提示する。
