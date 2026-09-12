---
name: plan-mode
description: >
  計画ファイルを作成して実装へ引き継ぐときに起動する。
  plan mode下、複数ファイル変更、多段階作業、バグ対応で起動する。
  バグ対応は単一ファイルの単純な修正でも起動対象とする。
  バグ対応を除く単一ファイルの単純な修正では起動しない。
---

# 計画モード

本スキルは、調査から計画ファイルの作成、実装及び起動経路別の終端までの工程制御を定める。
計画ファイルの成果物契約は`references/plan-file-standards.md`、実装と実行レビューの内部手順は`${CLAUDE_PLUGIN_ROOT}/share/`配下のタスク文書を正本とする。
計画は要件・外部仕様の水準で書き、計画のレビュー工程を置かない。計画の起草者が続けて実装し、実装後の実行レビューは要件・外部仕様の水準だけを対象とする。

ユーザーが`agent-toolkit:plan-mode`を直接起動した場合は、`references/grilling.md`に従いユーザーとの共通理解へ到達するまで確認を繰り返す。
起動プロンプトが起動経路として`agent-toolkit:process-wi`と`agent-toolkit:fast-process-wi`のいずれかを明示している場合は`references/grilling.md`を使わず、ユーザーの選好に依存する確認事項だけをUWIへ登録する。
`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`を受領したレーン担当として起動された場合は、UWIを自ら登録せず、同書の完了報告が定めるエスカレーションで確認事項を呼び出し元へ返し、回答を受領してから工程を続ける。
既存の旧単一ファイル形式・旧二ファイル形式の計画を読むときと、計画書式の読み取り互換の実装・検査を変更するときだけ、`references/legacy-plan-file-standards.md`を全文読む。

## 進め方

1. 適用規範、変更対象、定義・参照・呼び出し元、既存テスト、生成・配布経路、類似実装のうち、計画ファイルへ書く内容を確定するために必要な範囲を調査する
2. 計画の変更対象又は採用方針を左右する未確定判断を、判断同士の依存関係とともに列挙し、`agent-toolkit/rules/01-agent.md`「協調と自律」の確認要否判定を適用する。直接起動では`references/grilling.md`に従って確認を完了し、`agent-toolkit:process-wi`と`agent-toolkit:fast-process-wi`の経路では確認事項をUWIへ登録する。レーン担当として起動された場合は、いずれも行わず呼び出し元へ返す
3. `references/plan-file-standards.md`を全文読み、`${CLAUDE_PLUGIN_ROOT}/skills/plan-mode/scripts/create_plan_files.py`で計画ファイルを作成する。作業種別が`バグ対応`の場合は`agent-toolkit:bugfix`の原因分析契約に従って計画ファイル（バグ）を先に埋める
4. `${CLAUDE_PLUGIN_ROOT}/skills/plan-mode/scripts/check_plan_file.py --reject-migration-warnings <計画ファイルの絶対パス>`を単独のコマンドとして実行し、直接返った終了コード0を確認する。同書「計画構造検査」が定める起動形を用いる
5. 起動経路に対応する次の1行だけを実施する

| 起動経路 | 手順4の後に実施すること |
| --- | --- |
| `agent-toolkit:plan-mode`の直接起動 | 「直接起動の実装と終端」に従う |
| `agent-toolkit:process-wi`のレーン担当としての起動 | `${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「計画の起草」が定める`計画作成完了`の報告を返し、呼び出し元の応答を待ってから同書の実装へ進む |
| `agent-toolkit:fast-process-wi`からの起動 | `../fast-process-wi/SKILL.md`の実行順へ戻り、主作業ツリーで実装する |

実装中に要件・外部仕様の水準の判断が新たに生じた場合は、実装を続ける前に計画ファイルの該当節を現在の結論だけが残る形へ編集し、経緯を`## 変更履歴`へ記録する。ユーザーの選好に依存する判断は手順2と同じ経路で確認する。

## 直接起動の実装と終端

直接起動では、当該要求を受領したメインが計画の起草、実装、実行レビューの反映及び保存までを自ら所有する。
専用worktreeを作成せず、要求を受領した時点の作業ディレクトリが属する作業ツリーで直接実装する。実装を他の主体へ委譲しない。

1. 計画ファイル、成立させる結果、ユーザー指示との差分を提示し、`AskUserQuestion`で承認を得る。承認までは計画ファイルを実装入力として確定しない
2. `agent-toolkit:writing-standards`の該当資料を読み、計画の`## 要件・外部仕様`に従って実装する。`## 検証`の近接検証を実行し、commitする。実装単位ごとのcommitとcommitメッセージは`agent-toolkit:commit`に従う
3. `atk review-table init ~/.claude/plans/<計画stem>.exec-review.tsv`で実行レビュー指摘管理表を作成する。`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って実行レビュー担当を起動し、収束まで反復する。指摘への修正はメインが実施し、修正commitの履歴統合は`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「レビュー修正の履歴統合」と同じ順序で行う
4. 実行レビューの収束後に`## 検証`の`全体検証`行のコマンドで検証する。`CIで代替`の計画ではpush後のCIの結果で判定する
5. `## 進捗ログ`へ完了判定を記録し、`atk plans commit <計画ファイル名>`で保存する。`## 終端工程`が挙げる操作を実施し、`agent-toolkit:completion-report`で報告する

本スキルの起動後は、計画ファイルを作成するまで対象規範配下（`agent-toolkit/`等のコーディングエージェント向け規範文書）を直接編集しない（計画を経ない直接編集で、ユーザーが合意していない規範が確定した事例に由来する。連続する直接編集はPreToolUseフックが遮断する）。
