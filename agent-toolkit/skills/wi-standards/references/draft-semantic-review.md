# WI原稿の意味の確認

本書は通常AWIと、報告用を除くUWIに適用する。
`agent-toolkit:report-completion`が定義する報告用UWIは、同スキルの`../../report-completion/references/report-formats-uwi.md`「報告用UWIの確認と保存」と`uwi-format.md`を用いる。

付随型の委譲元、専念型の投入前レビュー担当、および直接起草するメインエージェントは、原稿、元の要求、実際の回答の出所、現物の状態、そして取得済みの証拠を独立して突き合わせる。
採否、成立性、完成判定の定義基準は`drafting-completion.md`にあり、wi-standardsの読込表において「調査に入る前」や「原稿を確定する前」に指定された共通基準を適用する。
確認側も、起草時に取得済みの証拠と保存結果を再利用する。
検収の品質水準は`agent-basics:review-standards`「文章成果物の実害」の作業項目の基準とし、意味を変えない表現だけの推敲は求めない。
元の要求がその場での回答や案の確定を求めている場合は、`agent-toolkit:user-confirmation-and-report`「成果物の制限」に照らし合わせ、その場で返答すべき回答内容と後続作業の完成条件とを明確に区別する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| WIの投入前検収の品質水準を適用する前 | `agent-basics:review-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/review-standards/SKILL.md` |
| add-awi-by-userまたはgrillingによるWI作成の考慮漏れを検収する前 | `../../add-awi-by-user/references/omission-check.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/references/omission-check.md` |
| 資料起点の採否を検収する前 | `agent-toolkit:add-awi-by-user/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/SKILL.md` |
| 参考資料に基づく候補の十分性を検収する前 | `agent-basics:writing-standards/references/design-heuristics-proportionality.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-proportionality.md` |
| 規範・手順による既存充足を検収する前 | `agent-basics:writing-standards/references/agent-documents-basics-ownership.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-ownership.md` |
| 依頼の`再発の経過を問う要求`が`該当`、または検収者が逐語本文・趣旨から失敗・誤り・指摘の反復、頻度、悪化、いつからか守られなくなったことへの言及と判断した要求を検収する前 | `agent-basics:bugfix/references/root-cause-severity.md`と`agent-basics:bugfix/references/root-cause-scenarios.md`を全文読む | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-scenarios.md` |
| 複数バグの現象・期待する契約・原因の対応を検収する前 | `agent-toolkit:wi-standards/references/analysis-records.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/analysis-records.md` |
| 報告用UWIの確認・保存手順へ切り替える前 | `../../report-completion/references/report-formats-uwi.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-uwi.md` |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 本文の参照基準を適用する前 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文の参照基準を適用する前 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/writing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/writing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-completion.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/user-utterance-correction.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance-correction.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `awi-body-content.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body-content.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:wi-standards/references/uwi-format.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/uwi-format.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-completion-decisions.md`、`drafting-completion-scope.md`、`drafting-completion-confirmation.md`、`drafting-completion-validation.md`、`drafting-completion-policies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-decisions.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-scope.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-confirmation.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-validation.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-policies.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `awi-body.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `uwi-format.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/uwi-format.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `origin-and-approval-origin.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval-origin.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-workflow-saving.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-saving.md` |
| 引用の認可や解釈のため出所を再取得する時 | `drafting-workflow-evidence.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-evidence.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-actions-hierarchy.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions-hierarchy.md` |

## ユーザーが読む部分

1. 原要求の対象・時点・結果は、起草者の要求分類を読む前に原文から確定する。

2. 検収の開始には、`atk wi add --dry-run`または`atk wi edit --dry-run`が検証済み原稿ごとに返すユーザー向け部分の保存ファイルを使う。
   AWIはH1タイトル行から最初のフェンス外H2直前まで、UWIは問い・理由・推奨する回答を含む冒頭から同じ境界までが、末尾空行も含め原文のまま抽出される。
   作成主体から渡された抽出結果を再利用し、要求識別子、入力原稿、抽出保存先、比較資料の対応を保つ。
   原稿全体の検収時に現行本文と抽出の一致を確認し、原稿が変わった場合はdry-runを再実行して新しい抽出へ更新する。
   検証・抽出保存に失敗した結果は使わず、原稿を修正して再実行する。
   現行原稿と受領済み抽出の一致は、新規・編集それぞれのdry-runへ`--compare-opening-file <受領済み抽出の絶対パス>`を指定して確認する。
   複数原稿では`--body-file`と同じ順に比較ファイルを反復指定する。
   改行・前後空白・末尾空行を含む完全一致だけを受理する。
   失敗時は原稿と抽出の対応を確かめ、変更した原稿を再検証して抽出を更新する。
   検収済みの原稿をそのまま保存する場合は、本保存（`--dry-run`なしの`atk wi add`、本文を置き換える`atk wi edit --body-file`）へ同じ`--compare-opening-file`を付けてよい。
   CLIは保存の前に同じ基準で比較し、不一致・読取不能・件数違いでは保存しない。
   比較の指定を全ての保存へ求めるものではなく、検収済み原稿との一致を保存時にも確かめる場合に使う。
   付随型の委譲元と専念型の投入前レビュー担当は、原稿全体を通読する前に、まず抽出部分のみを用いてwi-standards冒頭の合格基準を判定する。
   直接起草するメインエージェントの自己判定においても同一の基準を用いる。
   AWIについては`awi-body-content.md`「節の構成と分担」に定めるH1直下の内容を判定し、UWIについては`uwi-format.md`「構成」の内容を判定する。

3. 読者別探索担当を起動するのは、同一の要求または同一の項目に関して、ユーザーが読む部分の分かりにくさをユーザー自身が指摘・是正している場合（逐語引用本文、UWIの`## 回答`、AWIの`## ユーザーコメント`など）、または検収者が基準に照らして合否を自力で判定できない名詞句を挙げた場合に限定する。
   担当者の起動は`share/reader-fit-review.parent.md`に従って行い、対象種別は「WIのユーザー向けの部分」、読者像は「wi-standards冒頭の用語基準が前提とする知識を持つユーザー」に設定する。
   同一の読者像に対して同時に確認可能な抽出内容は、要求との対応関係を保ちながら、既存の`成果物`配列と1対1で対応する`比較資料`配列へとまとめる。
   新規作成の項目については、各要素を`{"比較元本文":"新規","今回差分":"全文","現行本文":"<抽出パス>"}`とする。
   渡す素材は抽出内容とその比較資料のみに限定し、原稿全体における要求の網羅性、由来、採用案、反映先、成立性の検証責任は、意味の検収主体が保持する。
   検収者は、返却された判定結果、指摘事項、および証拠と抽出部分を突き合わせ、指摘から漏れている説明不足な語や多義的な語があれば、自ら指摘事項に追加する。
   一括起動に用いた識別子と、各抽出内容に対する判定、指摘、証拠を要求ごとに正しく対応付ける。
   基準を満たしていない原稿のみを修正が必要な原稿として作成主体へ差し戻す。
   再発防止策についても、ユーザー向けの語彙で表現できているかを判定する。
   委譲元が修正例を提示する場合も、本文における処置の階層構造を維持する。
   再判定は、変更が加えられた抽出内容とその直接的な影響範囲を対象として行い、本文と契約境界に変更がない抽出内容については、親契約に定められた再点検省略条件を適用してそのまま再利用する。
   資料不足や処理の失敗は要求と読者ごとに記録し、親契約が定める補完、回復、終了観測の手順によって回収する。

## 原稿全体と返却の検収

1. add-awi-by-userまたはgrillingによるWI作成では、原文・原稿・調査根拠から`../../add-awi-by-user/references/omission-check.md`の全類型を独立して点検する。
   範囲の限定には調査母集団も含め、起草者の点検結果だけで充足としない。
   ユーザー固有の未確定判断があれば指摘へ含め、add-awi-by-userのメインが同じセッションで確認し、回答・認可・未達時の扱いを本文へ記録してから合格とする。

2. 効率改善案では、採用案との比較前に、検収者が変更前の現物から通常入力と集約・共有・再利用の便益を独立に確かめ、ユーザー目的から導く保持条件へ対応付ける。
   主経路の削減に加えて、変更が及ぶ経路で失う便益と増える処理を比較し、構造の成立と全体の費用の妥当性を分けて検収する。

3. 原稿・既存記録・CLI保存出力の直接参照から根拠を確認する。
   必要情報が揃っていれば別の証拠要約を求めず、不足する出所・判断・検証との対応だけを既存記録へ補わせる。

4. 代表AWIによる共通本文の参照は`cross-repository-submission.md`「独立したAWIへの分割と依存」に従い、参照先と固有本文を合わせて検収する。
   共通原因分析や引用の表は、明示された代表AWIの指定箇所から取得できれば充足とする。

5. 完成条件の網羅性・観測可能性を確認する前に、`drafting-completion-validation.md`「投入前の読み直し」の必要性判定を適用し、起草者が加えた条件も独立して判定する。
   原因と被覆の判定では、同節に従い、探索範囲の各限定を根本原因の成立条件から導けるか確認する。
   資料起点の採用案は、`agent-toolkit:add-awi-by-user`冒頭の資料比較に従い、資料の具体的な手法と対象の利用結果を結ぶ根拠を確かめる。
   規定の存在や対象外のテスト例だけで既存充足や必要性を確定していないかも比較する。
   規範・手順による既存充足は、`agent-basics:writing-standards/references/agent-documents-basics-ownership.md`「既存規範による被覆の判定」の読込経路で確かめたかを確認する。
   元の問いの分析単位を原文から独立に確定し、原因候補を選んだ前提と対応するか判定する。
   依頼の`再発の経過を問う要求`が`該当`、または検収者が逐語本文・趣旨から反復・頻度・悪化・守られなくなった時期への言及と判断した要求は、読込表の2資料を全文読み、個別事象に限定せず分析する。
   原稿が個別原因と再発経過（過去対策、未適用・範囲不足・効果不足・別原因の区分、残る原因条件）を分けていなければ、作成主体へ差し戻す。
   複数バグでは、`analysis-records.md`「原因分析の記録先」に従い、各バグの現象・期待する契約・原因の対応が一意かを確認する。

6. 確定した原要求の対象・時点・結果と、原稿の対象別結論・証拠の母集団を比較する。
   将来の確認方針だけで今回の対象別検討を完了にしない。
   原稿全体に対して、次の資料を適用する。

   - `drafting-completion-decisions.md`
   - `drafting-completion-scope.md`
   - `drafting-completion-confirmation.md`
   - `drafting-completion-validation.md`
   - `drafting-completion-policies.md`
   「重複判定」は同節の起点別の適用条件に従い、免除された既存項目との比較を実施していないことだけを理由に検収を止めない。
   観測された不具合を起点としbugfixを起動して起草するAWIには「原因と定義の所有者」と同資料が参照するbugfixの基準も併せて適用する。
   原文、実際の回答、現物の状態、ならびに取得済みの証拠をもとに、要件の分類、処置の妥当性と必要性、一連の処理全体の一貫性、根本原因と対象範囲の網羅、過去の判断との整合性、ならびに未確認範囲の有無を独立して判定する。
   原稿が課している操作上の制約が、原文で指定された工程に正しく対応しているかも確認する。
   原文と反映内容を読み比べ、`agent-toolkit:delegation/references/writing.md`「ユーザー入力素材の取扱い」に従って要求の脱落・追加・意味の変化を確かめる。
   認可や解釈のため出所の再取得が必要な場合は、`drafting-workflow-evidence.md`「出所と観測の取得」に従い、取得済みの出所ファイルと記録位置を優先し、不足時に指定された会話IDから取得する。
   通常の転記は取得済みの原文で確認する。
   任意節や引用位置の記録がないこと、`wi-quote-check`の未実行だけを理由に差し戻さない。

   - 渡した要求の集合と、返却された要求識別子が重複なく完全に一致していることを確認する。
     複数の原稿が存在する場合も、各要求に対して個別に基準を適用する。
   - 調査の先送りを含まないことを確認する。
     調査や棚卸しの実施自体を作業要求とした原稿や、判断基準のみを提示して対象ごとの具体的な判断を後続作業に残した原稿は、`drafting-completion-confirmation.md`「確定の判定」に基づき修正が必要な原稿として差し戻す。
     手動作成の原稿では、`wi-standards-body.md`「起票目的別の調査出口」を適用する。
   - 付随型における`起草結果`の状態、結果、続行できない理由、および省略値が、`share/add-wi.subagent.md`「起草の返却」の仕様と一致していることを確認する。
     専念型による結果は、`share/wi-draft-review.subagent.md`「出力」の仕様と一致していることを確認する。
   - 原稿および証拠ファイルの絶対パスが実在して読み取り可能であり、AWIは`awi-body.md`の基準を、UWIは`uwi-format.md`の基準を満たしていることを確認する。
     分割した要求について、受け取った割当先と内容の説明が対応することを確認する。
   - 既存項目との比較を実施する場合は、再利用や部分的な包含関係を既存の本文と共通基準の「重複判定」に基づいて独立に確認する。
     再利用を決定した項目については新規保存せず、既存項目をそのまま投入結果として扱う。
   - `判断の変更`を行う場合は、確定要件の対象、範囲、および外部から観測可能な結果と矛盾せず、追加する手段や条件が共通基準に照らして必要性を満たしていることを確認する。
   - `condition_not_met`を返す場合は、投入が不要であると判断した結論と明確な根拠を示す。
     人間由来の要求を投入対象外とする場合は、`origin-and-approval-origin.md`「承認」に従って確定したうえで、後続の保存対象から除外する。
   - 先行項目の正式なファイル名が未確定であっても、要求内容や証拠から参照先および参照箇所が一意に特定できる状態であれば、意味の検収を進めてよい。
     正式なファイル名への置き換え、および保存後の一致確認は、作成主体が`drafting-workflow-saving.md`「投入」の手順に従って実施する。

7. 委譲元および専念型の投入前レビュー担当は、原稿と成立性の証拠を確認し、対策・回帰防止策の行と再発防止策の2行を比較する。
   4区分の候補行のいずれかが欠けていないこと、採否の行が候補ごとの採否と影響・費用の比較を示し候補数を採用義務として扱っていないことを確かめる。
   再発防止策の行に挙げられた問題が通常の対策範囲の外にあることを確かめ、`agent-basics:bugfix/references/root-cause-actions-hierarchy.md`「処置の階層」における持ち込み試験を実施する。
   成立しない原稿は、処置を対策行か回帰防止策行へ移して反映内容節の詳細と対応付け、再発防止策の選び直しまたは不採用理由の記録を求めて作成主体へ差し戻す。

## 初回と再判定・差戻し

1. 初回の検収では、入力時点で判断できる成立性の問題と、ユーザーが読む部分における用語の課題を一括して返す。
   不足を指摘する際は、採用案の成立性や要求達成にどのように影響するかという理由を必ず対応付ける。
   共通基準に従って、投入前に確定すべき公開契約、必要な設定値への到達性、ならびに開始・終了・途中失敗からの再開可否を独立して確認する。
   成立性の結論に影響しない内部設計、実装単位、検証手順の具体化などは、計画工程に委ねる。
   再判定する際は、前回の指摘事項の解消状況、修正に伴う差分とその直接影響、および新たに追加された要求を明確に切り分け、新規の指摘事項を「前回から残存している不足」「修正に起因する不成立」「要求追加」のいずれかに分類する。
   全文は整合性を確認するための文脈として読み取り、修正範囲外について新たな内部設計を要求することはレビューの範囲外とする。
   ただし、前回提示されていなかった重大な不成立を発見した場合は、根拠を添えて差し戻す。
   原稿・出所・証拠側の不足は作成主体へ修正を求める。
   検収主体が取得可能な未読、切り詰めの未回収、原文や証拠との突き合わせの未実施は返却前に自身で完了させ、指摘理由にしない。
   読者別探索の実施・省略根拠も同じ区別で確認する。
   許可された取得手段を尽くしても到達できない入力は、その入力を必要とする要求に限定して共通契約の「続行できない区分」「続行できない理由」の2行で返し、試した手段と解除に必要な入力を示す。
   一部の要求が修正待ち、資料不足、または読者確認の失敗となっている場合であっても、すでに合格基準を満たした他の要求の保存処理を進める。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 作業完了報告とセッションの完了処理の基準を適用する前 | `../../report-completion/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |
| AWIとUWIの共通規範：通常AWIの本文の基準を適用する前 | `wi-standards-body.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/wi-standards-body.md` |
