---
name: plan-mode
user-invocable: false
description: >
  対象リポジトリを変更する作業で計画ファイルを作成し、
  計画から実装・レビューへ引き継ぐ場合に起動する。
  作成要否は本文「計画ファイルの作成要否」に従う。
---

# 計画モード

本スキルは調査から計画ファイルの作成、実装および`起動経路`の値ごとの終端までの工程を定める。
工程の責務は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`、計画ファイルの成果物契約は`references/plan-file-standards.md`が定める。WI本文の要求と由来は`agent-toolkit:wi-standards`、実装と実行レビューの内部手順は`${CLAUDE_PLUGIN_ROOT}/share/`配下の`<役割名>.subagent.md`が定める。
計画は要件・外部仕様の水準で書き、起草者が続けて実装する。レビューは実装後の実行レビューだけで行う。

計画バンドルの作成・検証・保存と実装への移行には、対象リポジトリ、プラグイン全体の参照資料、ファイル操作とCLIが必要である。機能が不足する環境では、提示された要件と資料から計画の原稿を返し、未確認の実装・検証・保存を明示する。正式な保存と実装は機能のあるClaude CodeまたはCodexで続け、原稿の文には[文章の品質基準](references/quality-standards.md)を使う。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| Codexで実行し、計画工程へ着手する前 | `references/codex-runtime.md` |
| 計画ファイルの初版を起草する前（「進め方」手順3） | `references/plan-file-standards.md`、`references/plan-structure-check.md`、`references/plan-file-storage.md` |
| 計画バンドルを`atk plans commit`で保存する前 | `references/plan-file-storage.md` |
| 保持、不変性、完全復元のいずれかを計画の契約か確認の選択肢へ書く前と、過去の契約へ戻す変更か過去の契約を撤去する変更を計画する前 | `references/plan-restoration-contracts.md` |
| `起動経路`がメインによる起動で、計画構造の検証を終えて実装へ進む前（メインが自ら実装に着手する直前） | `references/main-launch.md`、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`冒頭の`未判定検証記録`の段落と「起動」の`Git管理外の変更`の段落 |
| 計画構造を検証する前 | `references/plan-structure-check.md` |
| 旧単一ファイル形式または旧二ファイル形式の計画を読む前と、計画書式の読み取り互換の実装・自動チェックを変更する前 | `references/legacy-plan-file-standards.md` |

## 確認と起動経路

確認の要否と手順、UWIへの退避は`agent-toolkit:user-confirmation-and-report`が定める。協調モードでメインが起動した場合は、同スキルの`references/grilling.md`に従ってユーザーとの共通理解に達してから起草する。入力のWIが確定した判断は確認し直さない。
`agent-toolkit:process-wi`のレーン担当は、確認事項を`agent-toolkit/share/rules-subagent.md`「確認事項の即時通知」で委譲元へ返し、回答を受けてから続ける（UWIの登録は委譲元が行う）。起草と待機の境界は`agent-toolkit:process-wi`の`references/lane-planning.md`が定める。

## 計画ファイルの作成要否

変更作業では原則として計画を作成し、要求の採否・恒久化・リファクタリング・再開情報を後続へ渡す（ユーザーの承認資料ではない）。バグ対応は原因分析資料（WIの`## 原因分析`か計画ファイル（バグ））との対応を保つため常に作成する。
省略できるのは、バグ対応ではなく、既存の値・文字列の差し替えで完結し、恒久化・リファクタリング候補が構造上生じない場合に限る。省略時の実行レビューは起動元の工程が定める。

## 進め方

本スキルの起動後にエージェント向け文書（`agent-toolkit/`など）を編集するのは、計画ファイルを作成した後とする。

1. 変更対象・外部仕様・テスト・検証を確定するために調査する。WIの確定事項は`references/plan-file-standards.md`「計画ファイルの構成」に従って参照し、再調査しない
   - 作業種別が`バグ対応`で、入力にWIが無いかWIの原因分析を訂正・追加する計画では、手順2の確認より前に`agent-toolkit:bugfix`を起動し、直接的原因を確定して「対策の選定」で原因側と症状側の案を比べる。WIの原因分析を用いる場合も、症状側の対処を確認する前に同じ対策選定を適用し、類似見直しの母集団、QとC・Dの区別、処置の層と完了の判定が同スキルの`references/root-cause-analysis.md`の基準を満たすかを確かめる。満たさない部分は原因分析の追加として扱う
   - 参照する側と別発生源（`references/plan-file-standards.md`「要件・外部仕様」）をたどり、変更対象・追随範囲を`## 要件・外部仕様`へ書く
   - 出力・診断件数・分岐結果を完了条件に使う場合は、生成主体と消費先、分岐の有効化条件、是正前の状態と是正後の期待値を対応付ける。是正前後で同じ値になる条件、対象分岐が無効な条件、成功時に抑制される出力の不在は識別条件にせず、公開状態か直接の契約テストを使う
   - 依頼・WIの明示的な禁止条件と採用手段は`## 実施内容`の同じ行へ書き、比べられるようにする
2. 変更対象や採用方針を左右する未確定判断を依存関係とともに列挙し（WIが確定した事項は除く）、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」を適用する。実装中に生じた同種の判断も同じ。手段は「確認と起動経路」に従う
3. `references/plan-file-standards.md`「初回起草の雛形」に案件の値を記入し、`atk run-script plan-create --`で作成する。バグ対応で入力にWIが無いかWIの原因分析を訂正・追加する計画では、`agent-toolkit:bugfix`の`references/root-cause-analysis.md`に従って計画ファイル（バグ）を先に埋める。WIの`## 原因分析`をそのまま用いる場合は計画ファイル（バグ）を作成せずWIファイル名で参照する
4. メインによる起動では`atk run-script plan-check -- --reject-migration-warnings <計画ファイルの絶対パス>`を単独実行する。レーン担当は`agent-toolkit:process-wi`の`references/lane-planning.md`が定める選定結果とレーン識別子付きの形で単独実行する。いずれも終了コード0を確かめる
5. `起動経路`の値に対応する次の1行だけを実施する

| `起動経路` | 手順4の後に実施すること |
| --- | --- |
| `agent-toolkit:plan-mode`のメインによる起動 | 「メインによる起動の実装と終端」に従う |
| `agent-toolkit:process-wi`のレーン担当としての起動 | `agent-toolkit:process-wi`の`references/lane-planning.md`が定める待機条件と、条件に応じた報告・通知・実装の手順に従う |

実装着手後の計画ファイルの扱いと要件判断の記録は`references/plan-file-standards.md`の計画凍結に、中断後の再開は同書「進捗ログ」に従う。

## メインによる起動の実装と終端

実装、実行レビュー、終端は`references/main-launch.md`に従う。実行レビューは`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って起動し、名前付き入力`未判定検証記録`は同書冒頭の規定どおり渡す。
記録は`atk run-script verification-record -- --output <未判定検証記録JSONの絶対パス> --plan <計画の絶対パス>`で生成する（WI直渡しには`--wi <WIファイル名>`を反復できる）。根拠の追記・置換とレビュー修正後の再生成は`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「実装」の「完成条件証拠の検証記録」に従い、更新したJSONの絶対パスを実行レビューへ渡す。
