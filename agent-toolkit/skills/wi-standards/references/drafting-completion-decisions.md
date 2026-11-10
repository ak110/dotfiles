# 起草完了の条件：採否と実現方式の確定

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 報告用UWIの確認・保存手順へ切り替える前 | `../../report-completion/references/report-formats-uwi.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-uwi.md` |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 観測事象を契機とする要求の採用を確定する前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md`、`agent-basics:bugfix/references/root-cause-actions-selection.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-evidence.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-actions-selection.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/writing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/writing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `awi-body-completion.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body-completion.md` |
| 一連の処理全体の成立を判定する前 | `agent-basics:bugfix/references/root-cause-actions-selection.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions-selection.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `awi-body-placement.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body-placement.md` |
| 類似見直しの限定探索と是正範囲を判定する前 | `agent-basics:bugfix/references/root-cause-severity-impact.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md` |
| 原因分析の記録が推定だけに依拠していないことを判定する前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-evidence.md` |

## 採否と実現方式の確定

効率化が目的のAWIでは、通常の利用場面とユーザー目的から全体の結果と保持する品質を定め、集約・共有・再利用の便益を保持条件へ対応付け、主対象で減る仕事と他対象で増える仕事、故障時の継続手段（無効化・単純な動作への復帰）の有無を比べる。
追加・拡張案は対象の撤去・単純化・現状維持と同じ通常の対象構成で比べ、撤去等を除く既存方針があっても`drafting-completion-policies.md`に従って導入目的を保てるか判定する。
費用には起動・共通読込・中継・結果検収、並行待ち、障害停止・調査修正の所要を含め、同じ仕組みの修正回数と関連WIを変更履歴・キューで確かめ、継続手段の設置の要否も含む結論を採否理由と成立性の証拠へ残す。

解決対象の事象の実在と技術的な主張は、実装コード、実行結果、または公式の一次資料で裏付けを取り、採否の根拠として本文に記述する。
ユーザーの発話や委譲元からの依頼文に含まれる過去の変更、出来事、既存の状態についての言及も技術的主張として扱い、差分や履歴、現物と一致しない場合は、実際に観測した結果を前提として採用する。
ユーザーのみが判断できる選好・要件・判断は逐語引用と由来の規定に基づいて扱い、裏付け調査の範囲は採否・実現方式・完成条件に関わる主張に絞る。
値や外部から見える動作を変更する要求では、エンドユーザーの呼び出し手段から、値の生成元、デフォルト値やプリセットなどの設定元、保持先、直接の消費先、そして最終的に観測される結果までを実装コードからたどり、該当する要素を`## 反映内容と反映先`および`## 完成条件`に対応付ける。
`drafting-completion-validation.md`「投入前の読み直し」で対象集合と除外を確認する際は、この集合を用いる。

採否と実現方式は投入前に1つの案に絞り込み、本文には確定した案のみを記載する。
不採用とした代替案は、採用案の根拠を理解するために必要な範囲で記録してよい。
観測された事象を契機とする要求では、採用の確定前に、対策が直接的原因と主要な害に確実に作用するかを確かめる。
得られる改善と残存する害を比較した妥当性には、`agent-basics:bugfix/references/root-cause-analysis-evidence.md`「直接的原因と事象時点の確定」を適用する。
`agent-basics:bugfix/references/root-cause-actions-selection.md`「対策の選定」も併用して確認する。
再発の主要な原因や害の発生源が残る案は、それを残す正当な理由を確定した上で採用する。
害の発生源を定める既存方針や仕様があることだけでは、残す正当な理由とせず、`drafting-completion-policies.md`「方針と判断の記録との比較」に従って変更案を扱う。
説明を追記するかどうかの採否は、完成条件を固定する前に、`agent-toolkit:delegation/references/writing.md`「文章の共通規定」にある「情報を加える前の判定」に従って確定する。
完成条件を固定する前に、起点となった資料の目的と、エージェントが選んだ中間手段（安全対策、検収用ログ、例外処理やフォールバックなど）とを明確に区別する。
その上で、新設機構を省いた既存の操作でも消費側に必要な結果が得られるかを`agent-basics:writing-standards/references/design-heuristics-mechanisms.md`「自作機構の兆候と自問」に沿って比較検討し、その結論を本文の適切な箇所に反映する。
検収の準備・実行・再試行にかかる負担と、期待される品質改善とを比較し、要求された結果を十分に判定できる、より負担の少ない観測方法を選択する。
効率化以外の要求では反復検証・総費用比較をその方法でしか判定できない場合に限り、効率化の実測と不採用の境界は`awi-body-completion.md`「完成条件」に従う。
横展開の処置や再発防止策として新設する要素、および完成条件で必須とする観測手段・比較・検収・工程については、明示的な要件、規範上の制約、または観測済みの欠陥に応じた必要性を要素ごとに示す。
処理を実行しない、あるいは延期するといった保護条件についても同様の根拠に対応付け、単なる可能性や安全側への倒し込みだけを採用理由にしない。
後続工程で使用するモデル、ツール、担当エージェント、操作方法を指定する場合は、その工程への適用を明示したユーザー発話か、独立した確認回答を根拠とする。
新しい処理の追加は、消費側の必要な結果から既存機構の流用・統合・追加しない案・新設を次の資料に従って比べ、効率化では専用計測・運用上成立しない入力の網羅を一律に加えない。

- `agent-basics:writing-standards/references/design-heuristics-screening.md`
- `agent-basics:writing-standards/references/design-heuristics-proportionality.md`
- `agent-basics:writing-standards/references/design-heuristics-decisions.md`
反復実行される処理では、実行ごとのモデル呼び出し、読み込み、照会、待機、再試行にかかるコストも比較し、後続の処理エージェントが方式を選択できるよう、結論と根拠を本文の適切な箇所に記録する。
目的や要件に関する既存方針と衝突する案は、`drafting-completion-policies.md`「方針と判断の記録との比較」に従って方針を変える案として確認へ回す。
手段に関する方針と衝突する要求は、`agent-toolkit/share/rules/common/01-agent.md`「QCDと3段判定」に照らしてQCDが明らかに向上する場合に限り、方針からの逸脱として採用する。
方針との整合性や変更の認可が確定しない間は、既存の確認経路で扱う。
なお、詳細な内部設計、変更対象ファイルの分割、実装単位、検証手順などは、投入後の計画工程で確定する。

## 実現方式の成立

実現方式が前提とする公開契約、実行環境の機能、および対象の実装が実際に存在することを現物で確認し、隣接する既存の機構、共通契約、類似の実装と整合しているかを実装コードを読み解いて判定する。
特定の値の成立、単一パスでの成功、設定操作の実行可能性といった局所的な成立は、採用案全体の成立性の一部にすぎない。
採用案を確定する際や原稿を返却する前に、以下の観点も確認する。

- 一連の処理全体の成立性: `agent-basics:bugfix/references/root-cause-actions-selection.md`「対策の影響範囲」に従い、対策が消費する資源と時間の総量、同じ資源を利用する既存の処理、上限が作用する単位を、一連の処理全体の制約と比較する
- 適用・除外の条件: 原因が作用する条件を生成側と消費側の現物で確認し、除外や適用の範囲を、機構の前提条件が成立する範囲と一致させる
- 設定の再現性: 設定の定義元と、配備・再作成・稼働環境への反映手順を比較し、手作業による変更よりも、リポジトリで管理される定義の修正を優先して検討する
- 対象リポジトリの規範との整合性: 変更の順序、段階間の移行条件、完了判定など、方式を制約する領域ごとの規約を、`awi-body-placement.md`「配置規約と導入目的の調査」に従って方式決定前に読み、提案方式と突き合わせる

整合性を確認できない案は投入せず調査を継続し、自力で取得できない資料やユーザーの判断を要する事項のみを「未確定事項と確認」として扱う。
成果物を新設・分割・移設・統合する方式を採用する場合は、`agent-basics:writing-standards/references/design-heuristics-quality.md`「設計品質の劣化確認」に沿って評価し、採否を左右する結論と再取得先を本文の適切な箇所に記録する。
観点ごとの確認プロセスは成立性の証拠に残す。
後続で処理を担当するエージェントが成立性を判断して着手できるよう、結論と根拠の再取得先を、`source`を持つ通常AWIの本文の適切な箇所に記載する。
整合性確認で比較した対象や個別の結果は証拠として残す。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 設計判断の手順と領域別の設計観点：案の採否と実施順の判定の基準を適用する前 | `agent-basics:writing-standards/references/design-heuristics-decisions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-decisions.md` |
| 設計判断の手順と領域別の設計観点：自作機構の兆候と自問の基準を適用する前 | `agent-basics:writing-standards/references/design-heuristics-mechanisms.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-mechanisms.md` |
| 設計判断の手順と領域別の設計観点：問題と手段の比例性の基準を適用する前 | `agent-basics:writing-standards/references/design-heuristics-proportionality.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-proportionality.md` |
| 設計判断の手順と領域別の設計観点：設計品質の劣化確認の基準を適用する前 | `agent-basics:writing-standards/references/design-heuristics-quality.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-quality.md` |
| 設計判断の手順と領域別の設計観点：設計案の予備選別の基準を適用する前 | `agent-basics:writing-standards/references/design-heuristics-screening.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-screening.md` |
| 起草完了の条件：方針と判断の記録との比較の基準を適用する前 | `drafting-completion-policies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-policies.md` |
| 起草完了の条件：投入前の読み直しの基準を適用する前 | `drafting-completion-validation.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-validation.md` |
