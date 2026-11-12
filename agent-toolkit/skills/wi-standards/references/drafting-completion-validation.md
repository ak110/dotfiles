# 起草完了の条件：投入前の読み直し

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `cross-repository-submission.md` |
| 観測事象を契機とする要求の採用を確定する前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md`、`agent-basics:bugfix/references/root-cause-actions-selection.md` |
| 類似見直しの限定探索と是正範囲を判定する前 | `agent-basics:bugfix/references/root-cause-severity-impact.md` |
| 原因分析の記録が推定だけに依拠していないことを判定する前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md` |

## 投入前の読み直し

代表AWIを参照する原稿では、`cross-repository-submission.md`「独立したAWIへの分割と依存」に従って参照先を取得する。
共通本文と固有本文を合わせて検収し、冒頭の独立可読性、自repoだけの完成判定、不要な依存待ちと他repo固有要求の混入がないことを確かめる。

まず`awi-body-completion.md`「完成条件」に従い、起草者が加えた条件を含めて元の目的に対する必要性と由来を判定する。
不要なエージェント起草条件は除き、有効な条件について以下の網羅性・観測可能性を確認する。
ユーザーの明示要求や必要な品質を変える場合は既存の確認手順へ渡す。

不具合を起点とする通常AWIでは、原因分析の表の掲載と確定を`agent-toolkit:wi-standards/references/analysis-records.md`「原因分析の記録先」の確認完了基準で判定する。
対策行と再発防止策行を`agent-basics:bugfix/references/root-cause-actions-hierarchy.md`「処置の階層」で比較する。
対策だけを再発防止策へ書いた原稿は対策行へ移し、反映内容節の詳細と対応付ける。
再発防止策行には、根本原因への作用と別形態の持ち込み試験、または不採用理由を残す。
本文の対象範囲（旧形式の`## 適用範囲`を含む）と証拠にある探索範囲の各限定（期間、場所、対象の種類、リポジトリ）を確認し、根本原因の成立条件から導けるか判定する。
限定の根拠が成立条件か`agent-basics:bugfix/references/root-cause-remediation.md`「類似見直し」の履歴の期間規定から導けない原稿は投入しない。
履歴の期間規定によらない探索が起点の現存対象を含まない場合は、成立条件と限定が合わない兆候として扱う。
重大と判定した原稿では、同書「類似見直し」の「起点の問題を伏せた走査」の記録も証拠で確認する。

類似見直しの限定探索であっても、`agent-basics:bugfix/references/root-cause-severity-impact.md`「QとC・Dの区別」が定める是正範囲全体を確実に確認する。
後述するユーザー明示集合の保護対象には、同節に示される既知の欠陥や、追加・強化された規定の既存対象も含める。
ユーザーが確定した判断を複数の通常AWIへ展開する場合は、判断ごとに各本文の反映箇所または非該当理由を対応付け、判断の一部が抜け落ちている集合は投入しない。

本文を単独で読み直し、以下の条件をすべて満たしているもののみを投入する。
報告用を除くUWIに対しては、`awi-body.md`の各節および原因分析に関する項目を除いて適用する。
複数のWIを連続して起草する場合であっても、各本文を独立して確認する。

- すべての節が`awi-body-content.md`「要求固有性と記載量」を満たしていること。
  要求に固有でない一般的な記述は除外するか、要求との関係を明確にする
- 要件と技術判断の分類が`drafting-workflow-requirements.md`「確定した要件と委譲元の判断」に従っており、原文および実際の回答出所から保存直前まで裏付けられていること
- 説明の追記が`drafting-completion-decisions.md`「採否と実現方式の確定」の必要性判定を経ており、質問への回答を無関係な独立の変更要求へ変換していないこと
- 各節が`awi-body-content.md`「節の構成と分担」に従い、他の節の内容の単なる再掲を排除していること
- 既存の値を変更する対象ごとに、`drafting-completion-policies.md`「方針と判断の記録との比較」の結果が本文に記載されていること
- 採用案が`drafting-completion-decisions.md`「実現方式の成立」の各項目を満たし、確認した結論と再取得先が本文の適切な箇所および成立性の証拠に記録されていること
  - 観測事象を契機とする要求では、`drafting-completion-decisions.md`「採否と実現方式の確定」に従い、対策が直接的原因と主要な害に確実に作用することも確認する
  - 採否や適用範囲を左右する技術的主張は、`agent-basics:writing-standards/references/investigation-sources.md`「根拠と主張の一致」に従い、入力の生成・変換手順と、実際の観測から保証できる範囲とを突き合わせて判定する
- 対象は、観測された不具合を起点としbugfixを起動して起草し、直接的原因を確定した通常AWIとする。
  `## 原因分析`を`agent-toolkit:wi-standards/references/analysis-records.md`「原因分析の記録先」に沿った構成で記述し、分析の確定を同資料の確認完了基準で判定する。
  以下の条件は、`agent-basics:bugfix/references/root-cause-severity-impact.md`「QとC・Dの区別」に照らして確認する。
  `agent-basics:bugfix/references/root-cause-remediation.md`「類似見直し」も適用する。
  `agent-basics:bugfix/references/root-cause-actions-hierarchy.md`「処置の階層」を併用する。
  `agent-basics:bugfix/references/root-cause-prevention-requirements.md`、`agent-basics:bugfix/references/root-cause-prevention-verification.md`も適用する。
  Qualityへの影響と重大度の判定に明確な根拠があること。
  調査母集団が根本原因から論理的に導かれ、探索の深さ、0件時の再検討、調査の終了が重大度ごとの基準を満たしていること。
  限定探索する場合は、範囲、限界、許容する残存リスクを明示し、ユーザーが指定した対象集合を確実に網羅していること。
  「再発防止策」と表記した処置には、作用対象となる根本原因の単位を併記し、その単位に直接作用する内容であること（テストであれば再発防止テストであり、今回の事象を検出するだけの回帰テストは回帰防止策行にあること）。
  原因分析表が直接的な原因として根拠とする値ごとに、該当セルか参照先の証拠に生成元との比較が示されていること。
  それらの値が`agent-basics:bugfix/references/root-cause-analysis-evidence.md`「直接的原因と事象時点の確定」における推定の条件に該当しないこと。
  推測のみに基づく原因は確定とみなさず、調査を継続するか、あるいは「続行できない理由」として報告すること
- 指示語の参照先が同一本文内に存在し、変更対象、根拠、対応内容、完成条件を本文単体から一意に特定できること
- タイトルおよび本文で既存の要素（スキル、工程、担当、hook通知など）を指す名詞句について、定義元の実在性と、読者が本文からその定義に到達できることを確認したこと。
  会話やコミットメッセージなど参照した記録における呼称も確認対象とする。
  別文書の節名・用語・略号の表記法は`agent-basics:writing-standards/references/writing-common.md`「文章の共通規定」に従う。
  新しい名称の命名は`agent-basics:writing-standards/references/defined-names.md`「命名の方針」に従い、長い正式名称であってもタイトルで不当に省略しないこと。
  ユーザーが読む箇所はwi-standards冒頭の合否基準を満たすこと
- 逐語引用と反映内容を原文と読み比べ、要求の脱落・追加・意味の変化と、ユーザーの回答とエージェントの説明の混同がないこと。
  分割した場合は要求内容と割当先が対応していること。
  `wi-quote-check`は複雑な取り込みに任意で使用できる
- 対象集合と除外が原文、`## 反映内容と反映先`、原因分析と整合していること。
  原因分析を含む場合は原因条件から導いた母集団も満たすこと
- 完成状態を目的、変更内容、原因分析と固有の補足条件から一意に判定できること
- 対象リポジトリの外部へ成果を反映する本文では、`awi-body-completion.md`「完成条件」に定める外部反映の完成状態を反映先ごとに判定可能であり、承認元を併記できない公開操作を完成条件に含めていないこと
- 完成条件ごとに、観測主体、到達手段、権限、起動条件、待機手順を調査し、同一セッション内で確実に成立する観測方法を選択していること。
  人手による確認、後日の確認、配布反映後の確認などは`awi-body-completion.md`「完成条件」の例外事項に該当するため、その正当な根拠が本文の適切な箇所に記載されていること
- 完成条件において内部の配置場所や名称を固定的に指定している場合、その名称自体が要件や公開契約によって明示的に求められているものであること
- 仕分けや要約をした本文であっても、`drafting-completion-scope.md`「対象集合の仕分け」に定める対応関係が正確に保たれていること
- 対象、適用範囲、外部から観測可能な結果、除外内容を新たに具体化した本文では、次を満たしていること
  - 判定軸と結果が所定の記録形式に沿って記載されていること
  - 判定軸は`../../user-confirmation-and-report/references/user-confirmation-and-report-authorization.md`「原文からの具体化を要する軸」に従う
  - 記録形式は`../../user-confirmation-and-report/references/user-utterance-incorporation.md`「原文の保持と取り込み」に従う

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 起草完了の条件：採否と実現方式の確定の基準を適用する前 | `drafting-completion-decisions.md` |
| 起草完了の条件：方針と判断の記録との比較の基準を適用する前 | `drafting-completion-policies.md` |
| 起草完了の条件：対象集合の仕分けの基準を適用する前 | `drafting-completion-scope.md` |
