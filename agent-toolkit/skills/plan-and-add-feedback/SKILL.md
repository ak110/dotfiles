---
name: plan-and-add-feedback
description: >
  計画作成からレビューまでを実施したうえで、実装の代わりにフィードバック投入で終える運用を実行するときに起動する。
  `/agent-toolkit:plan-and-add-feedback`又は「計画してフィードバック投入で終えて」等の指示で起動する。
---

# 計画作成とフィードバック投入による終了

本スキルは、レビュー済み計画を後続処理へ引き継ぐ手順を提供する。本スキルはplan mode外で実行する。計画ファイルの作成・改訂と
フィードバック投入以外の変更を対象リポジトリへ加えない。
フィードバックの共通概念、本文、由来及び投入は`agent-toolkit:feedback-standards`を正本とする。ユーザー起動経路では条件付き重複判定を実行しない。

本スキルを起動したセッションの追加指示は、同一主題の計画本文又は計画型フィードバックへ反映する。
対象リポジトリは実装しない。
完了報告後も同一主題が続く限りこの扱いを維持する。
計画更新と直接実装の境界が不明な場合は更新前にユーザーへ確認する。

## 入力分岐

引数の判定と、ファイル名モードで必要な全項目の検証を、計画調査より前に完了する。

- 引数が全てキューの正規ファイル名形式ならファイル名モードとする。混在入力又は存在しない正規ファイル名は自然言語要件へ読み替えず入力エラーとし、状態を変更しない。
- 上記以外の入力は自然言語要件モードとし、従来の計画作成・レビュー・計画型フィードバック投入の経路を適用する。

同一セッションで、既に扱った同じ計画又は計画型feedbackを後続の方針に基づいて改訂し、別の処理経路が明示されていない場合は、本スキルの再開として扱う。自然言語modeとファイル名modeのいずれでも対象リポジトリを実装せず、計画の更新を検収し、計画型feedbackの投入までを完了する。更新後に実装承認を求めず、投入したfeedbackファイル名、計画ファイル及び実装へ着手していないことを固定完了報告として返して終了する。

## ファイル名モード

通常型フィードバックファイル名を1件以上明示した場合は、同一対象リポジトリの全対象を計画調査より前に`planning`へ一括移動する。同じplanning集合は再開できるが、process-loopの着手対象にはしない。全入力をファイル名昇順で1組の計画へ統合し、レビュー収束後に最古の項目を本文と`plan_file`の同時編集で計画型へ変換してinboxへ移す。変換結果を再取得し、残りが1件以上なら1回の`atk mq rm --force`で除去し、残りが0件の単一入力ではrmを呼ばず成功終端する。自然言語要件を受領した場合は、従来どおり新しい計画型フィードバックを追加する。

1. 全入力をファイル名昇順に並べ、同一`target_repo`のinbox通常型feedbackで`plan_file`を持たないことを一括検証する。TBD、既存計画型、別対象リポジトリ、欠落又は混在状態があれば、追加調査と状態を変更せず入力エラーとして返す。
2. 検証に成功した場合だけ、`atk mq start-planning <filename>... --target-repo=<repo>`を1回実行する。planningへ移す前後の対象集合、保存本文及び単一遷移commitを照合する。
3. 計画作成、レビュー及び確認待ちの再開では、入力として確定した対象worktreeの絶対パスを保持する。計画時の旧worktreeパスが本文に残っていても、実行時に渡された対象worktreeへ解決し、対象外のworktreeを操作しない。
4. 計画レビューまで完了した後、最古の項目だけを対象に、
   `atk mq edit <oldest> <message> --plan-file=<main-plan-absolute-path> --depends-on=<filename>... --target-repo=<repo>`を実行する。
   `message`は計画型feedback本文とし、依存は全入力の外部依存を初出順で統合して対象自身を除く。絶対かつ実在するメイン計画パス、計画のベースcommit、`source: plan`及び計画へ記録した要求単位の由来を同じ原子的編集で保存する。
5. 最古の変換結果を再取得し、`source`、本文、`plan_file`、`target_commit`、依存及びinbox配置を照合する。planningに残る項目が1件以上なら、統合先ファイル名と計画パスをnoteへ記録した1回の`atk mq rm <filename>... --force --note=<統合先ファイル名と計画パス>`で残りだけを除去する。単一入力で残りが0件ならrmを呼ばない。
6. 計画型編集前に中断した場合は、全対象を`atk mq return-to-inbox <filename>... --state=planning`で一括して戻す。変換開始後は最古の項目を戻さず、現在の差分、upstream包含、保存本文及びplanning件数を再取得して、滞留commitのpush又は未完了のrmだけを前方回復する。対象外差分又は別項目のprocessing移動を検出した場合は追加操作を止める。

ファイル名モードを再実行する場合は、全対象がplanningにある初期再開、又は昇順最古だけが期待する計画型metadataを持つinboxにあり、残りがplanning又は統合済みとしてactiveから消えている部分完了再開だけを受理する。部分完了では、保存本文と計画ファイルの`## 提示素材`にある元ファイル集合を照合してから、残っているplanning項目のrmを再開する。それ以外の状態混在、対象集合・計画パス・対象リポジトリの不一致は状態を変更せず停止する。

## 自然言語要件モード

1. 作業途中で本スキルが起動された場合は、現時点までの調査結果を計画へ引き継ぎ、実装せずフィードバック投入でセッションを完了する意図として扱う。既存の未コミット差分を変更せず、確認済みの事実だけを計画へ再利用する。
2. 複数リポジトリの場合だけ、`${CLAUDE_PLUGIN_ROOT}/skills/feedback-standards/references/cross-repository-submission.md`も全文読む。
3. 計画に使うworktreeの絶対パスとbase commitを保持する。
4. 実行主体が`agent-toolkit:plan-mode`をSkill機能で起動し、対象worktreeと調査済み事実を渡す。実装委譲を除く調査、確認及び計画ファイル初版の起草を完了する。
   起草完了後、実行主体が`atk managed-temp create --prefix plan-review-baseline`を単独で実行し、標準出力の絶対パスを保持する。その絶対パスを含めて`plan-review-executor`を起動する。
   計画ファイルの絶対パス、対象リポジトリ、プロジェクト規範、元のユーザー指示と提示素材の出所・引用範囲を渡し、計画構造検査・自己監査・レビュー担当の起動・指摘の配送・修正の検収・収束判定を委譲する。
   起動後は計画ファイルの書込所有権が`plan-review-executor`配下の計画担当へ移る。実行主体は完了報告を受領するまで計画ファイルを読み取り専用として扱い、起動文で書込主体を指定しない。
   `status: needs_escalation`を受領した場合は、事象、根拠、必要な判断をユーザーへ確認する。`status: completed`時の回収は、後段の`cleanup_evidence`必須検収に従う。
5. 完成後、実行主体が`agent-toolkit:feedback-standards`をSkill機能で起動し、本文、対象worktreeの絶対パス、base commit、plan file、source `plan`、要求単位の由来、依存及び吸収元のファイル名を渡す。新しい`inbox(plan)`のフィードバックを追加する。

計画を投入せず終了する場合や継続不能時は、確認済みの元本文を入力として`agent-toolkit:feedback-standards`をSkill機能で起動し、source `plan`と要求単位の由来を明示して同一セッション内で再投入する。元項目をrejectで計画へ吸収する経路は持たない。

自然言語要件モードで`plan-review-executor`を直接起動した実行主体は、`status: completed`で`cleanup_evidence`を受信する。受信する`cleanup_evidence`の項目は`plan`、`managed_temp`、`rereview_count`、`baseline_not_saved`、`rounds`を含む。
`rounds`の各要素は`round`、`target_plan`、`previous_files`を含む。`previous_files`の各要素は`path`、`bytes_after_save`、`sha256_after_save`、`bytes_before_rereview`、`sha256_before_rereview`、`mechanical_diff`を含む。
`mechanical_diff`は`current_path`、`exit_code`、`verified`を含み、ここまでの全項目を必須とする。
計画レビュー収束後、計画ファイルの実在と分量を照合する。再レビュー0回では、`cleanup_evidence`の`plan`と`managed_temp`が作成時の保持値に一致することを照合する。`rereview_count: 0`、`baseline_not_saved: true`、`rounds: []`であり、専用領域に`round-*.previous`の前回版が存在しないことも照合する。
再レビュー1回以上では、`baseline_not_saved: false`であり、`rereview_count`と`rounds`の要素数が一致することを照合する。各再レビューについて保存した全前回版、保存直後と再レビュー直前のバイト数・SHA-256、機械差分の検収結果が当該計画、現存する前回版と該当ラウンドへ対応することも照合する。`verified: true`も必須とする。
計画ファイルの実在と分量の照合だけでは回収前照合の成功としない。`cleanup_evidence`と必須項目が揃い、該当する回収前照合に成功した場合に限り、保持した絶対パスを`atk managed-temp cleanup --path <計画レビュー用managed temp領域の絶対パス>`へ渡して回収する。欠落、不一致、中断又は失敗時は領域を保持し、計画レビューを成功として扱わない。

本スキルは協調モードで動作する。ユーザーの選好は計画確定前に確認し、完成済み本文を`feedback-standards`へ渡した後は問い直さない。

## 完了報告の形式

投入したフィードバックファイル名、計画ファイル、吸収元、対象リポジトリ及び実装へ着手していないことを報告する。種別は`計画型`とする。
