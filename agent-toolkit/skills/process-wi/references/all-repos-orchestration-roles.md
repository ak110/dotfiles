# 登録全体の統括と対象別担当への受渡し：担当の役割

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 不良・是正要求・改善点を観測または受領した時と、ユーザーが時機の指示として「即時対応」や同義の表現を使った時 | `agent-toolkit:process-wi/references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |
| 配下担当の進捗を点検する時 | `../../delegation/references/waiting-and-monitoring-progress.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-progress.md` |

## 担当の役割

**1.** 統括担当が最上位のメインとして、全対象の判断・確認・完了報告を所有する。

要求内の追加ファイルの編集はレーン担当の判断に委ね、重なりを理由とする事前通知や選定記録の更新は要求の対象から除く。
競合は対象リポジトリの直列統合で扱う（`../../../share/exec-subagent-implementation.md`「実装」）。
常駐から起動した対話CLIには通常の最上位セッションの契約を適用する。
担当の選定は`all-repos-starting.md`「開始と振り分け」に従い、最も重い対象を統括が直接担当する。
全対象が上限1以下なら統括が集約し、上限2以上が混在する場合の軽い残対象群は1担当へまとめ、重い残対象は個別に並行委譲する。
各担当は自身の対象でprocess-wiを完遂し、対象別の結果と残工程を統括へ返す。
統括は直接担当の処理中も、他対象からの確認・結果の受領と全体統括を継続する。
統括は巡回開始時刻とタイムゾーンを全体作業契約へ記録し、リポジトリ担当の起動時に既存の`whole_contract_path`で渡す。
通常の継続は同じ担当を保ち、負荷の変動や軽い対象の終了だけを理由とする交代は対象外とする。
終了済み担当から回復する場合だけ、既存の終了観測・所有移譲の後、今回の未完了集合から直接担当を選ぶ。
配下担当の確認事項・担当外の発見・改善点は統括へ届け、報告用UWIは統括がprocess-wiの実行全体で1回作成する。
配下担当は祖先の対話CLIを終了する`atk agents-exit-session`を呼ばず、返却と終了観測を統括へ渡す。
統括自身は対象別結果を保存した後、最上位の終了手順で統括CLIを終了する。

**2.** 統括は自身が観測した不良・是正要求・改善点と配下担当からの報告へ、`immediate-fix.md`「即時対応」の対処時機判定を適用する。

受領時に処置の根拠と残る前提を検収し、独立した別要求はその時点から起草を進める。
投入準備の前倒しと独立受付は`finish-session-publishing.md`「公開担当の起動と並行工程」に従う。
委譲先が不要・担当外とした事象も、統括が根拠と保持すべき成果を確かめて処置を決める。
今回追加とした正式改善AWIは対象リポジトリ担当へ渡し、保存済みの正式名、選定への追加、担当への追送を確認する。
最小の直接修正の担当・基点・時機は同書「レーンの変更範囲へ加える修正の認可」で決め、統合済みの対象の独立修正をレーンへ反復追加しない。
回避と正式対応AWIだけの報告では、次のセッションで同じ停止が起きない根拠を確かめる。
根拠が無ければ、書き込みを担う配下担当へ直接修正を指示し、その検証結果を受領する。
配下担当を待つ間の進捗点検では、`../../delegation/references/waiting-and-monitoring-progress.md`「成果物側の観測」に従い、活動の有無と工程の所要・反復の妥当性を分けて判断する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 登録全体のAWI処理：開始と振り分けの基準を適用する前 | `all-repos-starting.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-starting.md` |
| 公開工程とセッション終了：公開対象が無い場合の短絡の基準を適用する前 | `finish-session-publishing.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session-publishing.md` |
