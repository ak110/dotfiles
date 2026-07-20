# 整合性チェック・codexレビューのバイパス機械検出の詳細規定

本ファイルは`integrity-checks.md`「変更履歴と変更内容の対応照合」
「整合性チェック・codexレビューのバイパス機械検出」「サブエージェント連携の設計整合性」の
各節から分離した詳細規定である。

## 変更履歴と変更内容の対応照合

`## 変更履歴`の各項目要約に含まれるバッククォートトークン（ファイルパス・節名アンカー）が
`## 変更内容`側H3見出し・対象ファイル一覧に対応するかを`pretooluse.py`が機械検査する。
既存の意味の同期確認（`integrity-checks.md`「計画文内・他ファイルとの整合」節）と両者を並行実施する。

## 整合性チェック・codexレビューのバイパス機械検出

`plan-file-creator`が内部で起動する各サブエージェント（`codexレビュー`・`plan-reviewer`）の起動は
次のセッション状態フラグへ記録される（`agent-doc-validator`は条件付きフラグとして扱う）。

- `plan_reviewer_invoked`
- `codex_review_invoked`
- `agent_doc_validator_invoked`は条件付きで扱い、対象は`## 変更内容`「対象ファイル一覧」に
  コーディングエージェント向け文書対象ファイルが含まれる計画とする。
  該当ファイル群: `agent-toolkit/rules/`・`.claude/rules/`・`.claude/skills/`・`agent-toolkit/agents/`配下、
  `agent-toolkit/skills/`配下、`.chezmoi-source/dot_claude/rules/`・`.chezmoi-source/dot_claude/skills/`配下、
  `AGENTS.md`、`CLAUDE.md`

記録は`agent-toolkit/scripts/posttooluse.py`が担う。
`agent-toolkit:plan-file-creator`配下から起動された場合の記録先・伝播経路は、`session-state-flags.md`のplan-file-creator関連フラグ項を参照する。
`agent-toolkit/scripts/pretooluse.py`は`ExitPlanMode`と`plan-impl-executor`起動
（Agentツール`subagent_type`判定）の両ハンドラを持つ。
上記2フラグ（条件成立時は`agent_doc_validator_invoked`を含む）の未起動時にブロックする。
フラグは新計画着手時（`agent-toolkit:plan-mode`スキル起動時）にリセットする。

## サブエージェント連携の設計整合性

スキル・サブエージェント定義・計画ファイル間の連携設計を点検する。

- 起動プロンプトへ埋め込む情報の必要十分性: 独立コンテキストで起動するサブエージェントが
  判断・実装に必要とする情報（対象パス・規範参照・成功条件等）が起動プロンプトへ漏れなく列挙されているか照合する
- 必要な規範スキルの呼び出し明記: サブエージェントが呼び出すべきスキル群
  （`agent-standards`・`writing-standards`・`review-standards`等）が計画本文で明示されているか照合する
