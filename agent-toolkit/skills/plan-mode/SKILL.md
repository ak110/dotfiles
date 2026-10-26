---
name: plan-mode
user-invocable: false
description: >
  対象リポジトリを変更する作業で計画ファイルを作成し、
  計画から実装・レビューへ引き継ぐ場合に起動する。
  作成要否は本文「計画ファイルの作成要否」に従う。
---

# 計画モード

本スキルは、事前調査から計画ファイルの作成、実装、そして`起動経路`に応じた完了までの一連の流れを定めている。
各工程の役割分担は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`、計画ファイルが満たすべき仕様（成果物契約）は`references/plan-file-standards.md`で規定する。また、WI本文の要求や由来の扱いは`agent-toolkit:wi-standards`、実装や実行レビューの詳細な手順は`${CLAUDE_PLUGIN_ROOT}/share/`配下の`<役割名>.subagent.md`に従う。
計画は要件や外部仕様の粒度で記述し、起草したエージェントがそのまま実装を進める。レビューは実装が終わった後の実行レビューのみで実施する。

計画バンドルの作成、検証、保存、および実装へ進むには、対象リポジトリへのアクセス、プラグイン全体の参照資料、ファイル操作、CLI実行環境が必要となる。これらの機能が揃っていない環境では、提示された要件や資料を基に計画の原稿を作成して返し、実装・検証・保存が未確認であることを明記する。正式な保存や実装は、必要な機能が利用できるClaude CodeやCodexで引き継ぎ、原稿の記述には[文章の品質基準](references/quality-standards.md)を適用する。

## 名指し参照の読込方法

本文で参照している資料は、それぞれの適用条件に合わせて取得し、内容を確認した上で具体的な判断を進める。起動時に指定された資料は、起動本文と一緒に提供される。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/references/grilling.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/grilling.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../process-wi/references/lane-planning.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lane-planning.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../bugfix/references/root-cause-analysis.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/exec.subagent.md#「実装」` | 判断 | `section:agent-toolkit:*/share/exec.subagent.md#「実装」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| Codexで実行し、計画工程へ着手する前 | `references/codex-runtime.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/references/codex-runtime.md` | |
| 計画ファイルの初版を起草する前（「進め方」手順3） | `references/plan-file-standards.md`の冒頭、「計画ファイルの構成」から「検証と公開工程」まで、「初回起草の雛形」「要件の成立性」 | 判断 | `section:agent-toolkit:*/skills/plan-mode/references/plan-file-standards.md#冒頭`、`section:agent-toolkit:*/skills/plan-mode/references/plan-file-standards.md#「計画ファイルの構成」から「検証と公開工程」まで`、`section:agent-toolkit:*/skills/plan-mode/references/plan-file-standards.md#「初回起草の雛形」「要件の成立性」` | |
| WIの原因分析を補うか計画ファイル（バグ）を書く前 | `references/plan-file-standards.md`の「バグ調査」 | 判断 | `section:agent-toolkit:*/skills/plan-mode/references/plan-file-standards.md#「バグ調査」` | |
| 進捗を記録する前と、中断後に再開する前 | `references/plan-file-standards.md`の「進捗ログ」 | 判断 | `section:agent-toolkit:*/skills/plan-mode/references/plan-file-standards.md#「進捗ログ」` | |
| 計画バンドルを`atk plans commit`で保存する前 | `references/plan-file-storage.md` | 操作 | `bash:atk plans commit` | |
| 保持、不変性、完全復元のいずれかを計画の契約か確認の選択肢へ書く前と、過去の契約へ戻す変更か過去の契約を撤去する変更を計画する前 | `references/plan-restoration-contracts.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/references/plan-restoration-contracts.md` | |
| `起動経路`がメインによる起動で、計画構造の検証を終えて実装へ進む前（メインが自ら実装に着手する直前） | `references/main-launch.md`、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`冒頭の`検証結果`の段落と「起動」の`Git管理外の変更`の段落 | 判断 | `read:agent-toolkit:*/skills/plan-mode/references/main-launch.md`、`section:agent-toolkit:*/share/exec-review.parent.md#冒頭` | |
| 計画構造を検証する前 | `references/plan-structure-check.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/references/plan-structure-check.md` | |
| `検証結果`を記録する前 | `${CLAUDE_PLUGIN_ROOT}/share/verification-evidence.md`の「完成条件証拠の検証記録」 | 判断 | `section:agent-toolkit:*/share/verification-evidence.md#「完成条件証拠の検証記録」` | |
| 旧単一ファイル形式または旧二ファイル形式の計画を読む前と、計画書式の読み取り互換の実装・自動チェックを変更する前 | `references/legacy-plan-file-standards.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/references/legacy-plan-file-standards.md` | |

## 確認と起動経路

確認する基準や手順、UWIへの退避方法は`agent-toolkit:user-confirmation-and-report`に従う。協調モードでメインエージェントが起動した場合は、同スキルの`references/grilling.md`に沿ってユーザーと認識を合わせてから起草する。入力となるWIで確定済みの事項は、改めて確認し直す必要はない。
`agent-toolkit:process-wi`のレーン担当は、確認事項が生じた際に`agent-toolkit/share/rules-subagent.md`の「確認事項の即時通知」に従って委譲元へ通知し、回答を得てから作業を再開する（UWIの登録は委譲元が担当する）。起草と待機の区切りは、`agent-toolkit:process-wi`の`references/lane-planning.md`で定める。

## 計画ファイルの作成要否

リポジトリを変更する作業では、原則として計画ファイルを作成し、要求の採否、恒久化、リファクタリング、中断時の再開情報などを後続の工程へ引き継ぐ（これはユーザー向けの承認資料ではない）。バグ対応では、原因分析資料（WIの`## 原因分析`または計画ファイル（バグ））との対応関係を保つため、必ず計画ファイルを作成する。
作成を省略できるのは、バグ対応以外の作業で、既存の値や文字列の差し替えだけで完結し、恒久化やリファクタリングの候補が構造上発生しない場合に限られる。省略した場合の実行レビューの手順は、起動元の工程の規定に従う。

## 進め方

本スキルを起動した後にエージェント向け文書（`agent-toolkit/`など）を編集する場合は、必ず計画ファイルを作成してから着手する。

1. 変更対象、外部仕様、テスト、検証内容を確定するために調査する。WIで確定している事項は`references/plan-file-standards.md`「計画ファイルの構成」に従って参照し、重複した調査は行わない。
   - 作業種別が`バグ対応`で、入力にWIがない場合やWIの原因分析を修正・追加する計画では、手順2の確認前に`agent-toolkit:bugfix`を起動する。直接的な原因を突き止め、「対策の選定」で根本原因への対策と対症療法を比較検討する。WIの原因分析をそのまま使う場合も、対症療法を採用する前に同様に選定し、類似箇所の見直し対象、品質とコスト・納期（QとC・D）の区別、対応の適用レイヤー、完了判定が同スキルの`references/root-cause-analysis.md`の基準を満たしているか確認する。不足がある場合は原因分析を追加する。
   - 参照側や別の発生源（`references/plan-file-standards.md`「要件・外部仕様」）をたどり、変更対象や影響が波及する範囲を`## 要件・外部仕様`に記述する。
   - 出力内容、診断件数、条件分岐の結果などを完了条件に設定する場合は、生成元と利用先、分岐が有効になる条件、修正前の状態と修正後の期待値を正しく対応付ける。修正前後で値が変わらない条件、該当の分岐が無効な条件、正常時に出力されないメッセージの不在などは判定基準にせず、公開されている状態や直接の契約テストで確認する。
   - 依頼やWIで指定された明示的な禁止条件と採用する手段は、比較しやすいよう`## 実施内容`の同じ行に並べて記述する。
2. 変更対象や方針の決定に影響する未確定の判断事項を、依存関係や前後関係を含めて整理する（WIで確定済みの事項を除く）。その上で`agent-toolkit:user-confirmation-and-report`の「確認要否の判定」を適用する。実装中に生じた同様の判断も同様に扱う。確認方法は「確認と起動経路」に従う。
3. `references/plan-file-standards.md`の「初回起草の雛形」に案件の具体的な内容を記入し、`atk run-script plan-create --`を実行して計画ファイルを作成する。バグ対応で入力WIがない場合や、WIの原因分析を修正・追加する場合は、`agent-toolkit:bugfix`の`references/root-cause-analysis.md`に沿って先に計画ファイル（バグ）を完成させる。WIの`## 原因分析`をそのまま使う場合は計画ファイル（バグ）を新設せず、WIファイル名で参照する。
4. メインエージェントによる起動では、`atk run-script plan-check -- --reject-migration-warnings <計画ファイルの絶対パス>`を単独で実行する。レーン担当の場合は、`agent-toolkit:process-wi`の`references/lane-planning.md`で定められた選定結果とレーン識別子を指定して単独実行する。どちらの場合も終了コードが0になることを確認する。
5. `起動経路`の値に応じて、以下の該当する処理を1つだけ実施する。

| `起動経路` | 手順4の後に実施すること |
| --- | --- |
| `agent-toolkit:plan-mode`のメインによる起動 | 「メインによる起動の実装と終了」に従う |
| `agent-toolkit:process-wi`のレーン担当としての起動 | `agent-toolkit:process-wi`の`references/lane-planning.md`に定める待機条件と、その条件に応じた報告・通知・実装手順に従う |

実装着手後の計画ファイルの扱いや要件判断の記録は`references/plan-file-standards.md`の計画凍結の規定に、中断後の再開手順は同書の「進捗ログ」に従う。

## メインによる起動の実装と終了

実装、実行レビュー、および完了の処理は`references/main-launch.md`に従う。実行レビューは`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って起動し、`検証結果`は同書の冒頭の規定に沿って計画の進捗ログに記録する。
記録の記述方法やレビュー指摘修正後の更新手順は、`${CLAUDE_PLUGIN_ROOT}/share/verification-evidence.md`「完成条件証拠の検証記録」に従う。

WIを入力とする計画の開始時は、WIの既存証拠の対象・版・前提との差だけを調べ、変化部分、残る問い、実装に必要な詳細を補う。一致する範囲の証拠と記述は再利用する。
