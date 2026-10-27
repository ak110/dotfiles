# メインによる起動の実装と終了

計画あり・なしを問わず初回実行レビューを起動する前に、`verification-evidence.md`の検証記録で取得版、入力・環境条件、要求別の観測箇所を確認する。レビュー担当には保存済み結果の適用可否を独立に判定させ、不足または影響差分だけを追加観測させる。

本書は、メインエージェントが`agent-toolkit:plan-mode`を起動した場合について、計画作成後の実装、実行レビュー、保存、および完了報告までの流れを定めている。
メインエージェント自身が計画の起草から実装、実行レビュー指摘の反映、計画の保存までを一貫して担当し、専用のworktreeは作成せず、要求を受けた時点の作業ツリーで直接実装する。
実行レビューが収束し、受領確認を完了した返却ファイル（`atk agents wait`が出力する`agent_message_path`）の絶対パスは、計画の進捗ログへ`受理済みの完成条件証拠`として記録する。後続の計画基準レビューを実施する際は、同名の任意入力としてこのパスを渡す（ファイルが読めない場合、指定を省略した場合、該当行が存在しない場合は`なし`とする）。参照の適用条件は、`agent-toolkit:review-standards`の`references/exec-review-recording.md`「受理済み判定への参照」に従う。
初回起動時と修正後の再レビュー時は、同parentが使用する構造化入力からプロンプトを生成・チェックする。
組み込みの委譲を起動または継続する場合は、`agent-toolkit:delegation`の`references/sending.md`にある生成CLIを使用し、同じ役割の入力宣言へと接続する。

1. 計画ファイルの`## 要件・外部仕様`に沿って実装を進める（成果物の品質基準は`agent-basics:writing-standards`の該当資料を確認し、配送・状態・工程記録を扱う場合は`agent-toolkit:writing-standards`の運用表も適用する）。
   - 実装に着手する前に、受入シナリオで規定された公開呼び出し手段、入力値と初期状態、期待される結果、および検出すべき失敗様態を具体化し、`agent-basics:writing-standards`の`references/testing.md`「検証単位の選定」に基づいてテスト手法を選定する。レビュー指摘への修正時も同様に適用し、不具合は該当する入力を用いて再現させた上で修正する。
   - Gitの管理対象外となっているファイルを変更する場合は、初回の変更前に事前のバックアップ（複製）を作成し、元ファイルと複製ファイルの対応関係を`## 進捗ログ`に記録する。
   - `### 受入シナリオ`の各行に対応する公開呼び出し手段を用いた結合テストまたはE2Eテストを実装に含め、変更範囲の検証を実行して自己検収する。呼び出し手段、入力、期待結果と実際の結果の対応、シナリオごとのテスト名と合否、未実施とした理由や条件変更の判断を`検証結果`として`## 進捗ログ`に記録する。その上で、自身が担当する残工程の担当者と再開先を記録して実行レビューへ引き渡す。記述方法は`${CLAUDE_PLUGIN_ROOT}/share/verification-evidence.md`「完成条件証拠の検証記録」に従い、手動での観測結果やテスト出力は同書の「手動観測の保存」に沿って、実行時の版とともにmanaged-tempへ保存する。取得後にリポジトリの版（コミット）が変化した記録を再利用する際は、`agent-toolkit:review-standards`の`references/exec-review-recording.md`「根拠の書き方」に従う。
   - 実装の最終コミットを作成する前に、変更差分を`agent-basics:writing-standards/references/design-heuristics.md`「設計品質の劣化確認」に基づいて評価する。設計品質の劣化が認められる場合は修正し、観点ごとの評価結論を検証結果へ記録する。
   - `## 検証`に記載されたコマンドを実行し、`agent-toolkit:commit`の規定に従ってコミットを作成する。
2. `${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って実行レビューを起動し、その結果を受領する。完成条件の証拠は、レビュー担当者が返却内の条件別判定行として記述する。画面表示に変更がある場合は、同一の対象HEADに対してユーザビリティレビューを並列で起動し、指摘修正後も同様に並列で再判定する。指摘事項はメインエージェント自身が修正し、修正コミットの履歴統合は`agent-toolkit:commit`の`references/history-rewrite.md`「修正方法の選択」に従って処理する。
3. 完了へ進む前に、実行レビューにおける未解決の指摘が0件であること（画面差分がある場合はユーザビリティレビューの結果が`合格`または`未確定`であることも含む）を確認する。レビュー返却の`想定外事象:`で報告された変更差分外の既存不良についても、同parentの手順に沿って適切な処置工程へ引き渡す。リポジトリ全体の自動チェックやCIの判定は、後続の公開工程で実施する。
4. `atk run-script plan-progress --`を実行して完了判定を進捗ログに記録し、`atk plans commit <計画ファイル名>`で計画バンドルを保存する。合意済みの公開範囲に基づき、`agent-toolkit:commit`の`references/publish.md`に従って変更を公開し、`agent-toolkit:completion-report`を用いて完了報告を行う。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-standards/references/exec-review-recording.md#「受理済み判定への参照」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「受理済み判定への参照」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/sending.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/sending.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/SKILL.md` |
| 実装する成果物の品質基準を選ぶ前 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/testing.md#「検証単位の選定」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「検証単位の選定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/verification-evidence.md#「完成条件証拠の検証記録」` | 判断 | `section:agent-toolkit:*/share/verification-evidence.md#「完成条件証拠の検証記録」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/verification-evidence.md#「手動観測の保存」` | 判断 | `section:agent-toolkit:*/share/verification-evidence.md#「手動観測の保存」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-standards/references/exec-review-recording.md#「根拠の書き方」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「根拠の書き方」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/design-heuristics.md#「設計品質の劣化確認」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-heuristics.md#「設計品質の劣化確認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/commit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec-review.parent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/history-rewrite.md#「修正方法の選択」` | 判断 | `section:agent-toolkit:*/skills/commit/references/history-rewrite.md#「修正方法の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |
