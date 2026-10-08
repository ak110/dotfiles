---
name: plan-mode
user-invocable: false
description: >
  対象リポジトリを変更する作業で計画ファイルを作成し、
  計画から実装・レビューへ引き継ぐ場合に起動する。
  作成要否は本文「計画ファイルの作成要否」に従う。
---

# 計画モード

本スキルは調査から計画ファイルの作成、実装および`起動経路`の値ごとの終端までの工程制御を定める。
計画工程とメインによる起動時の実行工程の責務は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に従う。
計画ファイルの成果物契約は`references/plan-file-standards.md`、WI本文の要求と由来は`agent-toolkit:wi-standards`が定める。実装と実行レビューの内部手順は`${CLAUDE_PLUGIN_ROOT}/share/`配下の`<役割名>.subagent.md`が定める。
計画は要件・外部仕様の水準で書き、レビューは実装後の実行レビューだけで行う。計画の起草者が続けて実装し、実行レビューは要件・外部仕様の水準を対象とする。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| Codexで実行し、計画工程へ着手する前 | `references/codex-runtime.md` |
| 計画ファイルの初版を起草する前（「進め方」手順3） | `references/plan-file-standards.md`、`references/plan-structure-check.md`、`references/plan-file-storage.md` |
| 計画バンドルを`atk plans commit`で保存する前 | `references/plan-file-storage.md` |
| 保持、不変性、完全復元のいずれかを計画の契約か確認の選択肢へ書く前と、過去の契約へ戻す変更か過去の契約を撤去する変更を計画する前 | `references/plan-restoration-contracts.md` |
| `起動経路`がメインによる起動で、計画構造の検証を終えて実装へ進む前 | `references/main-launch.md` |
| 計画構造を検証する前 | `references/plan-structure-check.md` |
| 旧単一ファイル形式または旧二ファイル形式の計画を読む前と、計画書式の読み取り互換の実装・自動チェックを変更する前 | `references/legacy-plan-file-standards.md` |

## 確認と起動経路

確認要否、質問手順とUWIへの退避は`agent-toolkit:user-confirmation-and-report`が定める。協調モードでメインが本スキルを起動した場合は、同スキルの`references/grilling.md`に従いユーザーとの共通理解へ到達するまで確認を繰り返し、その後に計画ファイルを起草する。
入力のWIが確定した判断は確認し直さない。
`agent-toolkit:process-wi`のレーン担当として起動された場合は、確認事項を`agent-toolkit/share/rules-subagent.md`「確認事項の即時通知」で委譲元へ返し、回答を受け取ってから工程を続ける。UWIの登録は委譲元が行う。計画の起草と待機の境界は`agent-toolkit:process-wi`の`references/lane-planning.md`が定める。

## 計画ファイルの作成要否

変更作業では原則として計画を作成し、要求の採否・恒久化・リファクタリング・再開情報を後続エージェントへ渡す。人間の承認資料とはしない。バグ対応では原因分析資料（WIの`## 原因分析`または計画ファイル（バグ））との対応を保つため常に作成する。

省略はバグ対応ではなく、既存の値・文字列の差し替えで完結し、恒久化・リファクタリング候補が構造上生じない場合に限る。省略時の実行レビューは起動元の工程が定める。

## 進め方

本スキルの起動後に対象規範配下（`agent-toolkit/`等のエージェント向け文書）を編集するのは、計画ファイルを作成した後とする。

1. 変更対象・外部仕様・テスト・検証を確定するために調査する。規範、定義と利用側、生成・配布、類似実装を手掛かりに必要範囲を定め、WIの確定事項は`references/plan-file-standards.md`「計画ファイルの構成」の関連WIの規定で参照し、再調査しない
   - 作業種別が`バグ対応`の計画のうち、入力にWIが無い計画とWIの原因分析を訂正または追加する計画では、手順2の確認より前に`agent-toolkit:bugfix`を起動し、同スキル「初動と拡張原因分析の判定」に従って直接的原因を確定する。原因を確定する前に対処を問う確認を組むと、選択肢が症状の側の案に偏る
   - 参照する側と別発生源（`references/plan-file-standards.md`「要件・外部仕様」）をたどり、`## 要件・外部仕様`の変更対象・追随範囲へ書く
   - 出力・診断件数・分岐結果を完了条件に使う場合は、生成主体と消費先、分岐の有効化条件、是正前の状態と是正後の期待値を対応付ける
   - 是正前の状態と期待値が同じ条件、対象分岐が無効な条件および成功時に抑制される生出力の不在は識別条件にせず、公開状態または直接の契約テストを使う
   - 依頼・WIの明示的な禁止条件（操作・機構・副作用）と採用手段は`## 実施内容`の同じ概念行へ書き、両者を比べられるようにする
2. 計画の変更対象または採用方針を左右する未確定判断を、判断同士の依存関係とともに列挙し（入力のWIが確定した事項は列挙の対象から外す）、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」を適用する。実装中に新たに生じた同種の判断にも本手順を適用する。確認の手段と`起動経路`の値ごとの扱いは「確認と起動経路」に従う
3. 読込表の`references/plan-file-standards.md`の全項を満たす計画を`atk run-script plan-create --`で作成する。作業種別が`バグ対応`の計画のうち、入力にWIが無い計画とWIの原因分析を訂正または追加する計画では、計画担当が手順1で起動した`agent-toolkit:bugfix`の`references/root-cause-analysis.md`の条件に従って計画ファイル（バグ）を先に埋める。WIの`## 原因分析`をそのまま用いる場合は計画ファイル（バグ）を作成せず、計画はWIファイル名で参照する
4. メインによる起動では`atk run-script plan-check -- --reject-migration-warnings <計画ファイルの絶対パス>`を単独実行する。`agent-toolkit:process-wi`のレーン担当は`agent-toolkit:process-wi`の`references/lane-planning.md`が定める選定結果とレーン識別子付きの形で単独実行する。いずれも直接返った終了コード0を確認する
5. `起動経路`の値に対応する次の1行だけを実施する

| `起動経路` | 手順4の後に実施すること |
| --- | --- |
| `agent-toolkit:plan-mode`のメインによる起動 | 「メインによる起動の実装と終端」に従う |
| `agent-toolkit:process-wi`のレーン担当としての起動 | `agent-toolkit:process-wi`の`references/lane-planning.md`が定める待機条件と、条件に応じた報告・通知・実装の手順に従う |

実行工程の計画ファイルの扱いと、実装中に生じた要件・外部仕様の判断の記録は、`references/plan-file-standards.md`の計画凍結に従う。
中断後に再開する主体が読む対象と順序は`references/plan-file-standards.md`「進捗ログ」が定める。

## メインによる起動の実装と終端

実装、実行レビュー、終端の工程は読込表の`references/main-launch.md`に従う。実行レビューは`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って起動し、名前付き入力`未判定検証記録`は同書冒頭の規定に従って渡す。
