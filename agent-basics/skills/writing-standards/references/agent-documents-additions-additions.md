# エージェント向け文書への追記と記述量の管理：規範追記時の判定

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 規範以外の品質改善案と投資先を比較する前 | `design-heuristics-screening.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-screening.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `quality-principles-qcd.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/quality-principles-qcd.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `design-heuristics-decisions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-decisions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `writing-readers.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/writing-readers.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `writing-common.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/writing-common.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `quality-principles-constraints.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/quality-principles-constraints.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-documents-basics-placement.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-placement.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-severity-impact.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-documents-basics-content.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-content.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `investigation-technical-clauses.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-technical-clauses.md` |
| 類似見直しと再発防止の完了を判定する前 | `../../bugfix/references/root-cause-prevention-verification.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-verification.md` |

## 規範追記時の判定

規範の不足や誤適用に対処するときは、まず誤解を招いた既存の書き方を直して目的を達成できるか検討する。
同じ目的を持つ既存の記述の統合・削除も検討し、追記は既存記述の書き換え・統合で目的を達成できない場合に限る。
同じ内容の定義を1箇所に保ち、原則として単純な構成にする。
改善の投資先は`design-heuristics-screening.md`「設計案の予備選別」の順に比較し、構造・標準ツール・検証で保証する範囲と、意図・認可・外部契約など文章で保持する条件を分ける。
追記・改訂する記述がQCD（`quality-principles-qcd.md`「QCDと3段判定」）の向上につながるかを判断し、貢献しない内容は書かず、既存の記述にあれば削除する。
改訂案が将来増やす作業量と継続的な保守費用は、採用前に`design-heuristics-decisions.md`「案の採否と実施順の判定」で比較・評価する。
規範・警告・hookなどがエージェントの行動を起こす条件を新設・変更するときは、読む主体と実際の利用場面から、次の2つの場面を挙げる。

- その条件が必要な対応を促す場面
- 不要な読込・警告・確認・停止を繰り返す場面

必要な対応を見逃す不利益と誤適用の継続費用を同じ比較に含め、必要な品質を保つ適用境界を採用前に確定する。
この比較は同節の採否判断の中で行う。
評価自体の負担を抑えるため、独立した審査工程や専用の計測を増やすことは避ける。
品質（Q）に影響する問題への対処として規定を追加する場合は、`../../bugfix/references/root-cause-severity-impact.md`「QとC・Dの区別」に従い、調査の深さや終了条件も同資料の重大度別の基準に合わせる。
規範案の起草と採否では、一般知識と固有の事実・硬い制約を区別する。
一般知識は価値・観点・代表語や短いチェックリストで想起できるようにし、固有のパス・公開契約・認可条件は具体的に残す。
詳しさの基準は、`writing-readers.md`「読み手別の追加注意点」のエージェント向け文書の項に従う。

成果物への記載を義務付ける条文を起草・改訂する場合は、その記載を読む主体と利用目的を同じ条文内に明記する。
読み手と用途を示せない場合は、成果物を作成する主体が対象事項を確認する記述にとどめる。
成果物の分量、識別子、構成に関する共通基準は`writing-common.md`「文章の共通規定」に集約し、成果物ごとの文書には固有の取り決めのみを記載する。

### 常時規範への追記条件

- 常時規範へ追記できるケースは、ユーザーが言葉で明示的に示した要件を規定にする場合、`quality-principles-constraints.md`「規定の区分と標示」の硬い制約を守る条件を追加する場合、および既存の条文を統合してファイル全体の記述量を減らす場合の3点に限る。
  その他の知見は、該当工程で起動するスキル、参照資料、hookの通知へ配置する。
- 常時読み込まれる規範（`AGENTS.md`や`CLAUDE.md`など）でスキルや`references/`の起動・読み込みを指示する場合は、起動契機、起動対象、そのスキルが扱う範囲のみを記載する。
  具体的な観点、判断手順、例外、具体例はスキル側のみに持たせる。
  両方に重複して記載すると、どちらを優先すべきか判断が難しくなる。
- 規範の中で起動対象として指定するツールやスキルは、その規範を読むエージェント自身が起動できるものに限定する（努力目標）。
- 規範、基準、目標値が存在するかどうかは、`agent-documents-basics-ownership.md`「既存規範による被覆の判定」に従って判定する。
- ユーザーへの発話に適用する規定は、その発話を担当する主体が読む定義元に集約し、守らないと意図が正しく伝わらない重要な条文に限定する（努力目標。発話ごとの読み込み量を抑えるため）。

### 失敗時の回復手順

- 失敗、拒否、上限超過、遮断といった異常時にのみ実行する手順（失敗時の回復手順）は、異常が発生した際に読み手に届く手段へ配置する。
  具体的には、ホストやCLIのエラー出力、hookの遮断通知に含まれる`fix`、`PostToolUseFailure`の`additionalContext`、異常発生を条件とする読込表が指す`references/`などがある。
  異常の有無にかかわらず全文が読まれる階層には、異常の種類と参照先を示す起動契機のみを記載する（努力目標。平常時の判断に不要な影響を与えないため）。
  対象は`agent-documents-basics-placement.md`「置き場所と配送」の読込階層のうち、常時ロード、役割別注入、スキル起動時、役割起動時とする。
  既存の仕組みで同様の手順がすでに通知される場合は、文書側の記述を削除する。
  読み手が異常を直接観測できない場合（エラー出力なしに成果物が破損する場合など）は事前防止の規定として記載し、手順の分量が起動契機と同等以下であれば移設せずにその場に残す。

### 工程と検証の追記

- 規範が適用されなかった問題への対策を検討する前に`agent-basics:bugfix`を起動し、`../../bugfix/references/root-cause-prevention-selection.md`「再発防止策」を利用する。
- 検証、レビュー、待機、再確認などの工程を追加・改訂する場合は、その工程が担保する成果と、先行工程やすでに存在する基準で達成済みの条件を確認し、追加が必要な条件のみを記載する（努力目標。利用されない記録や重複する工程を増やさないため）。
  工程を足す案と範囲を広げる案では、対象プロジェクトの既存の実行記録から1回の所要と通常の実行回数を取り、採否の比較に書く。
  条文を書き足す手間を実行の費用の代わりにせず、記録が無い場合は未計測とし、追加する工程の適用条件を限定する。
- 不具合対策として作業を制限する規定を設ける際は、原因への直接対策と、症状を抑えるための制約が守る価値とを比較する。
  原因への対策によって必要な成果を確保できる場合は制約の要否を再検討し、制約を残す場合は見直しの契機を記載する。
- 将来の観測結果を削除条件とする暫定的な規範は、追記の時点でその条件がまだ満たされていないことを実際の値で確認し、条件成立後に目指すべき状態も同じ粒度で記録する。
- 直接操作や代替手段に関する要件を記述する場合は、操作の開始に必要な対象と入力を、同一のコンテキストから取得できる手順とともに記載する。
- 品質要件を追加する際は、生成側（起草時や実装時の完了条件）と検出側（レビュー観点、自動チェック）の双方へ同時に反映する（努力目標）。
- 複数対象の調査・起草は、同じ担当が似た対象から順に処理して知見を再利用する構成を初期案とする。
  分担は、起動、規範や背景の重複読込、指示作成、検収、結論の再構築までの総費用で比較し、有利な場合に似た対象をまとめて渡す。
  件数や独立性だけで担当を増やさず、分割する理由を明記する。
  成果物の作成担当と文脈を共有しない担当によるレビューと、書込所有権に基づく分担は維持する。
- 完了条件は、その工程が達成すべき具体的な成果として記述する。

生成側と検出側への追加に関して、再発防止のセルフチェック観点のみは検出側に限定して配置する例外とする。
この例外を適用するのは、`../../bugfix/references/root-cause-prevention-selection.md`「再発防止策」の条件をすべて満たす場合に限る。
一般的な品質要件の追加については、原則どおり双方へ同時に追加する。

計画やレビュー契約に実装順序、作業単位の分割、個別コマンドなどの詳細を記載するのは、既存の規範や確定済みの外部仕様から一意に導けない場合、または手順を誤ると安全性、データ保全、公開契約、権限境界を損なう恐れがある場合に限定する。

### 条文の形式

- 守るべき価値、基本方針、例示、適用範囲の記述形式は`agent-documents-basics-content.md`「条件と範囲の記述」に従い、役割別の配置は同資料「置き場所と配送」に従う。
- 判断の分岐や報告・成果物の記載項目は、個別の事例を列挙するよりも、全体を貫く判定基準で表現できないかを先に検討する。
  個別列挙を残す場合は、列挙外のケースの扱いと対象範囲を同じ条文内に記載する（明確に定義しないと狭く解釈され、本来適用すべき場面で見落とされる恐れがある）。
- 数値の閾値や複雑な判定手順を設ける際は、より単純な原則で代替できないかを検討し、閾値を設定する場合はその境界値の根拠を本文に残す。
- 実行環境やモデルのバージョン情報を含む条文を作成する場合は、`investigation-technical-clauses.md`「エージェント向け文書の技術的条文」に従う。

### 既存対象への波及

- 改訂対象の判断ルールと同様の処理を行う既存の規定・生成側・消費側を見直す前に、`agent-basics:bugfix`を起動し、`references/root-cause-remediation.md`「類似見直し」の母集団導出と判定手順を適用する。
  判断の根拠は既存の改訂記録に残す。
  新しい場面別の条文が一般則の対象範囲を狭めたり、相反する指示を含んだりしていないかの確認にも同じ判定を用いる。
  品質（Q）に影響する問題への対処として規定を追加した場合は、努力目標の表記にかかわらず、次の2節に基づいて既存対象の修正範囲と完了基準を決定する。
  `agent-basics:bugfix/references/root-cause-remediation.md`「類似見直し」を適用する。
  `../../bugfix/references/root-cause-prevention-verification.md`「類似見直しと再発防止の完了」も併用する。
- 同一の事象に対して相反する判断を下す条文を共存させない。
  矛盾する既存条文がある場合は、同じ変更で書き換えるか優先順位を明確にし、別リポジトリの所管であれば該当リポジトリの担当へ変更を引き継ぐ。
- 努力目標ではない規定を新設・強化した場合は、その規定が適用される既存の成果物も確認し、作業権限の範囲内にある違反はそのセッション内で修正する。
  権限外の箇所は担当者へ必要な変更を引き継ぐ。
  これには規定追加前に作成され、追加後も継続して利用される成果物も含まれる。
  重大度に応じた未知の問題の探索終了と、修正対象の範囲は区別して扱う。
  努力目標の規定は、新しく作成する記述から適用する。
- 記録先を新設・改名・移設する改訂では、同様の内容の記録を求めている既存条文を、新しい記録先への参照へ書き換える。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| バグ対応の基準を適用する前 | `../../bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| エージェント向け文書品質（基本）：定義の所有者の基準を適用する前 | `agent-documents-basics-ownership.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-ownership.md` |
