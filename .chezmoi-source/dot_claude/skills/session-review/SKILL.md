---
name: session-review
description: >
  セッション終わりにユーザーが手動で実行する振り返りスキル。
  CLAUDE.mdへの学びの反映、pyfltr・agent-toolkitスキルの改善点を点検する。
  「セッション振り返り」「振り返り実行」「session-review」などのキーワードでユーザーが明示的に依頼したときに使用する。
disable-model-invocation: true
---

# セッション振り返り

ユーザーが任意のタイミングで実行する手動起動の振り返りスキル。
以下3項目について改善提案を列挙する。
ナレーション・見出し・反省過程の言語化は付けず、1項目1行で
「対象ファイル — 一文要約」の形で書く。
提案が無い項目には「指摘無し」とのみ書く。
当該セッションで利用しなかった項目（例: pyfltr未使用）はスキップしてよい。

## 1. CLAUDE.md / CLAUDE.local.md 追記候補

`find . -name "CLAUDE.md" -o -name "CLAUDE.local.md"`で候補ファイルを列挙する。
今後のClaude作業に必要な、本セッションで判明したプロジェクト固有の知見のみを対象とする
（観点: bashコマンド・コードスタイル・テスト手法・環境設定の癖・警告/落とし穴・繰り返された修正指示など）。
1コンセプト1行で簡潔に書く。
最初の提示は「対象ファイル — 一文要約」のみとし、ユーザー承認を得てから`Edit`で文面・差分を確定する。

- `CLAUDE.md`: チーム共有（gitコミット対象）
- `CLAUDE.local.md`: 個人・ローカルのみ（gitignore対象）

## 2. pyfltr

対象: pyfltr本体の挙動・メッセージ、および`plugins/agent-toolkit/skills/pyfltr-usage/SKILL.md`。
反映はユーザーが別途行う前提のため、提案までにとどめる。

## 3. agent-toolkit

対象: `agent-toolkit`プラグイン（スキル・フック・サブエージェント）と
`~/.claude/rules/agent-toolkit/`配下のルール。
反映はユーザーが別途行う前提のため、提案までにとどめる。
