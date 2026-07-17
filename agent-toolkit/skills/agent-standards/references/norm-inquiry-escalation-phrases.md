# 規範照会・是正要求フレーズ集（隔離リファレンス）

`agent-toolkit/rules/02-collaboration.md`「協調モード」節のメタ視点バレットの典拠である。
`agent-toolkit/scripts/_norm_inquiry_escalation.py`が対象とする検出フレーズの典拠でもある。
配布物フックは`UserPromptSubmit`経路（`agent-toolkit/scripts/user_prompt_submit.py`）でこの辞書を参照する。

`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節に従い、
機械チェック用辞書の検出語そのものは本ファイルへ隔離する。
メインエージェントは本ファイルを読み込まず、`Explore`サブエージェント経由で確認する。
修正が必要な場合はAgentツールで`subagent_type: claude`を起動して行う
（`plan-implementer`は実装委譲の専用先であるため本用途では指名しない）。
pyfltr機械チェックの対象からも除外する（`pyproject.toml`の`extend-exclude`へ登録する）。

実装用の正規表現定義（`_NORM_INQUIRY_PHRASES`）は
`agent-toolkit/scripts/_norm_inquiry_escalation.py`が唯一の格納先とし、本ファイルへは複製しない。
本ファイルはカテゴリ定義・代表フレーズの規範説明に限定する。

## 対象カテゴリと代表フレーズ

| 識別子 | 対象観念 | 代表フレーズ |
| --- | --- | --- |
| `norm-inquiry` | 既存規範・仕様の変更有無や制定根拠を問う疑問文 | 「勝手に規範を変えたのか」「これはルールに違反していないか」「〜のはずなんだけど」「いつの間にか形骸化してる」 |
| `correction-request` | 既存挙動への言及を伴う明示的な修正指示 | 「直して」「間違ってる」「〜を修正して」「今後の方針にして」 |

両カテゴリとも、単純な質問・要望一般（規範・ルール・既存挙動への言及を伴わないもの）は検出対象外とする。
検出は疑問符相当の文末表現（「か」「かな」等の終助詞、または全角疑問符）または明示的な修正動詞を伴う場合に限る。

## 機械検出の適用範囲

配線経路は`UserPromptSubmit`（`user_prompt_submit.py`）の1系統のみとする。
検出時の出力・クールダウン判定は同ディレクトリの`claude-hooks.md`
「出力フィールドの使い分け」節`### UserPromptSubmit`小節に従う。
セッション状態キー（`user_prompt_counter`・`norm_inquiry_last_injected`）のSSOTは
本スキル本体（SKILL.md）「セッション状態フラグ」節に置く。

## 検出時の挙動

検出時はブロック（`decision: "block"`等）をせず、
`hookSpecificOutput.additionalContext`でメタ視点点検・恒久化検討の実施を促すリマインダーを注入する。
過剰検出を避けるためクールダウン判定を併用し、同一指摘対応の作業中は再注入しない。
