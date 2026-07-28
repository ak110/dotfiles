---
name: process-feedbacks
description: >
  対象リポジトリのフィードバックを取得・検討・適用するときに起動する。
  フィードバック本文を投入するときにも起動する（「〇〇をフィードバックにして」等）。
  「フィードバックがあった」「改善提案を反映」「振り返り結果を反映」などのキーワードで起動する。
# session-reviewスキルのステップ3出力を主な入力として想定するが、
# 任意のフィードバック文面を受け付ける。
---

# フィードバックの投入・取得・批判的検討・適用

`atk mq`の全サブコマンドは内部で`git pull --ff-only`を実行する。手動`git pull`は不要。
自律実行モード下では`agent-toolkit/rules/01-agent.md`「協調と自律」節の運用を本スキルの
処理単位（フィードバック全件の採否確定と対象リポジトリのcommit・push完了）へ適用する。

## 基本方針

各採否判定の最優先基準は品質向上への寄与とする。形式整合のみを根拠とした判定を避ける。
フィードバックを処理する際は、言及された対象だけでなく、使い勝手・整合性の観点で
関連する改善点にも能動的に気付いて提案する（例: 新設した表示欄の隣接欄も併せて整備する）。
気付いた改善は、主題と根本原因を共有するか否かで扱いを分ける。主題の是正を取り消したときに当該事象も解消する改善（主題と根本原因を共有する改善）は`atk mq add --type=tbd`で記録しつつ本計画へ実装まで含める。根本原因が主題と独立する改善は`atk mq add`でフィードバックとして登録し、当該セッションでは実装しない。本規定の対象は改善提案に限り、`agent-toolkit/rules/01-agent.md`「完遂と先送り」節が同一セッション内対応を義務づける機能不全（既存バグ・既存のテスト失敗・既存lint違反・既存の品質ゲート指摘）は対象外とし、同節の規定に従う。
確認事項の回答が現状維持（設定・コードの変更を伴わない判断）を結論とする場合、
判断根拠と適用範囲を対象リポジトリの開発者向け文書へ記述してから採用処理する。
専用の記述先が無い場合は関連する既存節へ追記する。

## フィードバック投入

投入依頼があった場合、本文の未確定表現・確認要求文言を投入前にユーザーと解消し、
事実として確定した記述へ書き直す。対象リポジトリは本文の主題から判定し、
判別困難な場合は`AskUserQuestion`で確認する。各メッセージは単体で読んで意味が完結する形に整え、
出典URL・関連提案との関係・対象を一意に特定する前置きを含める。

投入前に`atk mq list --status=active --target-repo=<repo-path>`（パスまたは正規化リモートURLで
指定する）で対象リポジトリ宛の既存フィードバック一覧を照会し、主題が重複するものの有無を確認する。
重複を検出した場合は新規投入せず、
既存本文を`atk mq show <FILENAME>`で取得して差分情報（実測値・再現手順・出典）を統合し、
`atk mq edit <FILENAME> $'<統合後の本文>'`で更新する。

投入は`cd <repo-path> && atk mq add $'<message>'`で行う。
`atk mq add`と`atk mq edit`の本文は、`$'...'`のANSI-Cクォートで囲む。
ヒアドキュメント・パイプ・ファイルリダイレクトは入力経路に使わない。
addは種別・TBDメタデータ（`type`・`scope`・`question_type`・`choices`）をCLIオプションで指定し、
MESSAGEの先頭frontmatterでは`target_repo`・`source`と追加メタデータだけを指定できる。
editのMESSAGEは論理本文として扱われ、先頭frontmatterで明示したメタデータだけを既存値へ上書きし、
未指定メタデータは保持する。

判断が必要な複数の確認事項を本文へ含める場合、各項目の書式は
`references/hold-with-tbd-inject.md`「投入コマンド」節に従う（疑問文要件を本体へ複製しない）。

投入対象が実装完了後に他リポジトリへの後続feedback投入を要する場合、feedback本文末尾へ
`### 完了条件と連鎖feedback`ブロック（実装完了条件・後続feedback全文・関連feedback ID）を明示する。
連鎖feedbackの検出・投入は「ステップ6: 連鎖feedbackの自律投入」で扱う。

投入する本文へ既存計画ファイルの絶対パスを書くと、機械分類（`_atk_mq_schedule.py`の
`detect_plan_impl_reference`）により計画実装型（`type: plan-impl`）として分類される。
結果として次回処理セッションが新規の計画作成を経ず、当該計画ファイルを再実装する対象として扱う。
実装要求ではなく経緯の参照として既存計画へ言及する場合は絶対パスを書かず、日付や作業内容で
特定する（`agent-toolkit:plan-and-add-feedback`経由で意図的に計画実装型として投入する場合は
この限りでない）。

## ステップ1: 入力の確定と初期スケジューリング

`/process-feedbacks <repo-path>`形式の引数が実在ディレクトリなら対象リポジトリとし、
実在ディレクトリでなければフィードバック本文の直接入力として扱う。引数省略時は
`git rev-parse --show-toplevel`の現リポジトリを対象とする。

`atk mq list --status=active --target-repo=<repo-path>`で1行一覧だけを取得し、
本文全文は取得しない。

計画実装型の大半は`atk mq add`投入時点で機械分類済みのため、ここでLLM分類が必要になるのは
主に通常型と、計画ファイルがまだ存在しない段階で投入された計画実装型候補である。
一覧で`unclassified`と表示された項目（`frontmatter-broken`は含まない）がある場合は、
Agentツールで`Explore`を`model: sonnet`のforegroundとして1回起動する。
`frontmatter-broken`な項目は選抜・分類の対象から除外され、修復TBD投入は
`atk mq schedule`が機械的に行うため、LLM分類委譲へは含めない。
委譲先は対象filenameごとに`atk mq show <filename> --target-repo=<repo-path>`を実行し、
未分類または本文変更済みの項目だけを読む。
計画実装型の判定は`references/plan-impl-feedback-flow.md`のSSOTを適用する。
通常型は依存条件と推定対象ファイルを分類し、計画実装型は計画ファイルの絶対パスを返す。

外部・ユーザー依存を検出した場合、委譲先は条件文を引用したTBDを
`atk mq add --type=tbd --scope=hold`で投入し、分類結果へ生成されたTBD filenameを含める。

委譲先は分類結果を所有者だけが読み書きできる一時JSONへ保存する。
続いて`atk mq schedule --classifications=<path> --target-repo=<repo-path>`を実行する。
メインへは処理対象、実行群、除外理由、TBD作成要求だけを返し、本文全文を返さない。
一覧に未分類項目が無い場合は`atk mq schedule --target-repo=<repo-path>`を実行する。

依存先消失・自己依存・依存循環のTBD作成要求がある場合は、該当filenameと破損内容を示すTBDを投入する。
該当項目の型と対象ファイルを維持した補正JSONへTBD filenameを設定し、
`atk mq schedule --classifications=<path> --target-repo=<repo-path>`で外部・ユーザー依存へ
機械更新して同じ選抜工程内で再計算する。恒常的な未成立を返した計算では繰越回数を更新しない。

`atk mq schedule`の応答に含まれる`frontmatter_broken_filenames`は、
修復TBDの投入有無を問わずfrontmatter破損項目を通知する診断出力である。
TBDの投入自体は`atk mq schedule`のCLIハンドラーが同一ロック内で機械的に行うため、
process-feedbacks側で追加操作しない。修復は`atk mq edit`ではなく対象ファイルを直接編集する
（`atk mq edit`はfrontmatter解析失敗時に処理を拒否するため）。
TBDの回答は、直接編集による修復が完了したことの確認として扱う。

選抜結果をinbox項目とprocessing残存項目へ分ける。
inbox状態の選抜対象だけを`atk mq start-processing <filename...>`でprocessingへ遷移させる
（`start-processing`はinboxだけを移動元として探索するため、processing状態のfilenameを渡さない）。
processing状態の選抜対象は遷移させず、中断前の処理をそのまま再開する。
選抜外の項目は理由付き繰越を記録し、processing残存分は
`atk mq return-to-inbox <filename...>`でinboxへ戻す。

target_repoが誤設定と判明した場合は、現在の本文を保持したまま
`atk mq edit <FILENAME> $'---\ntarget_repo: <正しいリポジトリ>\n---\n\n<現在の論理本文>'`で更新する。
editがcommit・pushまで完結するため`atk mq commit`は続けて実行しない。
当該分は本セッションの処理対象から外す。

## ステップ2: 内容の調整

各提案の追記先と抽象度を最適化する。詳細は`references/content-adjustment.md`・
`references/review-checklists.md`「内容調整チェックリスト」節に従う。

## ステップ2.5: 網羅調査と保留判定

各提案の追記先となる既存記述・関連スキルを横断調査する（観点は
`references/review-checklists.md`「網羅調査チェックリスト」節）。複数件の場合は
`Explore`サブエージェントへ個別並列委譲する。

初期スケジューリング後に本文から新たな依存条件が判明した場合だけ分類結果を修正する。
未成立なら`atk mq schedule --record-deferral dependency-unmet:<filename> --target-repo=<repo-path>`で
理由と繰越回数を記録してinboxへ戻す。全項目の前提条件を本ステップで再調査しない。

保留と判定した時点で既にprocessing状態へ遷移済みの対象は、`atk mq return-to-inbox <filename...>`で
未処理状態へ戻したうえで次回セッションへ持ち越す。

取得したフィードバックの全件が保留判定となり、当該セッションで採用・実装へ至る件数が0件となる場合、セッションを終了せず、各保留の確認手段をOR条件で結合した待機を1回起動する。待機ループは180秒間隔で各条件を評価し、起動から3時間を上限とする。待機条件は`agent-toolkit/rules/02-claude-code.md`「サブエージェント運用」節が定める待機ループ規定（1回のツール呼び出しへ閉じ込める、起動前に実データへの単発ヒットを確認する、タイムアウト時は能動的に状態を調査する）に従う。起動前に各条件を単発実行し、観測対象を取得して評価できることを確認する。いずれかの条件が充足した時点で全保留を再評価し、充足済み項目のみ保留を解除して採否判定を再実行する。未充足項目は保留を維持する。再評価後も採用・実装へ進む項目が0件の場合は、未充足の保留だけでOR条件を再構築し、起動時に確定した上限時刻を変えずに待機ループを継続する。3時間以内に採用・実装へ進む項目が生じた場合は以降の工程へ進む。上限到達時点で採用・実装へ進む項目が0件の場合は、一部の条件が充足済みであっても残存保留を維持して振り返り工程（ステップ8）へ進む。

## ステップ3: 批判的検討

判定観点は`references/review-checklists.md`「批判的検討チェックリスト」節に従う。
単一要求は採用・不採用の二値、複数の独立要求を含む場合は採用／部分採用／不採用で判定する。
原文の反映内容を自己判断で薄めて部分採用しない（過剰と判断した場合も原文どおり採用か不採用の二択）。

## ステップ4: 検討結果の提示

詳細は`references/decision-format.md`に従う。採否ラベルと反映概要を記録する
（`atk mq adopt`等の実行はステップ7で行う）。

## ステップ5: 計画作成と実行

`agent-toolkit:plan-mode`スキルに従い計画を作成して実行する。採用フィードバック全件へ
`agent-toolkit:plan-mode`の類似見直し（母集団の悉皆点検）を実施する。計画実装型のみ、
または通常型の採用0件の場合は本ステップを実施しない。`## 実行方法`へ
本スキルの呼び出しを書かない（後続工程はメイン側が完了報告の受領後に別途進める）。

計画分割は次のいずれかに該当する場合のみ行う（規模・作業量等の自己推定は分割根拠に含めない）。

- 外部管理issue由来（issue番号・トラッカーURL参照があり紐づけが必要な場合）
- フィードバック本文中の処理順序・グルーピング指示がある場合
- 既存計画ファイル参照（`references/plan-impl-feedback-flow.md`の判定基準に該当する時）

計画の実行工程へ他リポジトリ向けのフィードバック投入を含める場合は、計画作成の時点で
投入予定先ごとに既存フィードバックの主題重複を照会し、重複がある場合は投入工程を計画へ含めない。
照会結果は計画の`## 調査結果`へ記録する。

## ステップ6: 連鎖feedbackの自律投入

対象は採用フィードバックが`### 完了条件と連鎖feedback`ブロックを含む場合とする。
実装完了条件を元feedback記載の確認手段で1件ずつ確認し、既定30分以内に全条件充足なら
後続feedback本文を`atk mq add`で投入する。未充足の場合は`atk mq add --type=tbd --scope=chain-feedback`へ
永続化し本工程を終了する。該当なしの場合は次工程へ進む。

## ステップ7: 採否確定の後始末

コミット・pushの完遂後、対象ファイルを後始末する。
push時点で採否は確定しており、CI失敗時も同一セッション内で追加commitにより是正するため採否は覆らない。
CI通過確認を待って後始末を保留すると、セッションが中断した場合に処理中の状態が残留して
次回セッションの再取得対象となる。後始末の完了後もCI通過確認は`agent-toolkit:commit`スキル
「push後のCI通過確認」節に従って同一セッション内で完遂する。

後始末の実施前に、自律実行中に自ら投入した確認事項がある場合、
`atk mq list --type=tbd --answered=yes --target-repo=<repo-path>`で対象リポジトリ限定の
回答済み確認事項一覧を再照会する。回答が記入されていた場合は暫定判断を破棄し、
回答内容に沿って実装をやり直したうえで後始末へ進む。

- feedback側の採用ファイル: `atk mq adopt <filename...> --note=<概要> --commit=<sha>`
- feedback側の不採用ファイル: `atk mq reject <filename...> --note=<不採用理由> --commit=<sha>`
- TBD側の回答済み採用ファイル: `atk mq adopt <filename...> --note=<概要> --commit=<sha>`
- 保留ファイルは後始末コマンドを実行しない。保留判定時点で未処理状態へ戻していない場合は
  `atk mq return-to-inbox <filename...>`で戻したうえで次回セッションへ持ち越す
- `--note`・`--commit`の詳細は`references/decision-format.md`「後始末コマンドの引数」節に従う

## ステップ8: 振り返りとセッション終了

`session-review-dotfiles`が利用可能な場合は先に起動し、その後`agent-toolkit:session-review`を
起動する（利用不可の場合は`session-review`のみ）。完遂後`agent-toolkit:exit-session`を呼び出す。

## 自己整合性の検査

詳細は`agent-toolkit/skills/agent-standards/references/feedback-review-common.md`
「自己整合性の検査」節を参照する。
