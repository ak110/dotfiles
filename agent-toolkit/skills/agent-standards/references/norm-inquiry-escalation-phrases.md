# 規範照会・是正要求フレーズ集（隔離リファレンス）

`agent-toolkit/rules/02-collaboration.md`「協調モード」節のメタ視点バレット項の典拠である。
`agent-toolkit/scripts/_norm_inquiry_escalation.py`が対象とする検出フレーズの典拠でもある。
配布物フックは`UserPromptSubmit`経路（`agent-toolkit/scripts/user_prompt_submit.py`）でこの辞書を参照する。

`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節に従い、
機械チェック用辞書の検出語そのもの（隣接する`_norm_inquiry_escalation_test_inputs.txt`）は
コーディングエージェントのメインコンテキストへ読み込ませない。
本ファイル（説明文）は同節内の除外規定に該当し、メイン・Exploreサブエージェント双方のRead参照を許容する。
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
| `correction-request` | 既存挙動への言及を伴う明示的な修正指示 | 「（既存挙動を）直して」「間違ってる」「〜を修正して」「今後の方針にして」 |

両カテゴリとも、単純な質問・要望一般（規範・ルール・既存挙動への言及を伴わないもの）は検出対象外とする。
検出は疑問符相当の文末表現（「か」「かな」等の終助詞、または全角疑問符）または明示的な修正動詞を伴う場合に限る。
`correction-request`の修正動詞（「直して」「修正して」）は、単独では通常の修正依頼と区別できないため、
既存規範・既存挙動への言及語を近傍に伴う場合のみ検出対象とする。
近傍語の具体一覧は実装SSOT`agent-toolkit/scripts/_norm_inquiry_escalation.py`の`_NORM_INQUIRY_PHRASES`を参照する。

## 機械検出の適用範囲

配線経路は`UserPromptSubmit`（`user_prompt_submit.py`）の1系統のみとする。
検出時の出力仕様およびクールダウン判定は同ディレクトリの`claude-hooks.md`
「出力フィールドの使い分け」節`### UserPromptSubmit`小節に従う。
過剰検出を避けるため同一指摘対応の作業中は再注入せず、
クールダウン間隔・セッション状態キー（`user_prompt_counter`・`norm_inquiry_last_injected`）のSSOTは
本スキル本体（SKILL.md）「セッション状態フラグ」節に置く。
