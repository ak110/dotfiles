# WIの調査・起草・保存の共通手順：投入条件の判定

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 観測した不具合・ユーザーの指摘・エラー・hookの誤動作を起点とする調査に入る前 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| セッション記録を復元・進捗・操作の観測へ使う前 | `../../review-session/references/session-records-conversation.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/session-records-conversation.md` |
| 依頼の`再発の経過を問う要求`が`該当`の場合に加え、起草者が逐語本文・趣旨から失敗・誤り・指摘の反復、頻度、悪化、いつからか守られなくなったことへの言及と判断した要求で、原因を確定する前 | `agent-basics:bugfix/references/root-cause-severity.md`と`agent-basics:bugfix/references/root-cause-scenarios.md`を全文読む | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-scenarios.md` |
| 原因分析の書式を使う前 | `agent-toolkit:wi-standards/references/analysis-records.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/analysis-records.md` |
| 原因分析の処置を記録する前 | `agent-basics:bugfix/references/root-cause-actions-hierarchy.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions-hierarchy.md` |
| 報告用UWIの確認・保存手順へ切り替える前 | `../../report-completion/references/report-formats-uwi.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-uwi.md` |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-severity.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-actions.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-prevention.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-completion-scope.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-completion.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-completion-policies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-policies.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `origin-and-approval-comments.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval-comments.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-completion-decisions.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-decisions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `states-and-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `awi-body-external.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body-external.md` |
| 保存する本文の共通品質を判定する前 | `agent-toolkit:delegation/references/writing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/writing.md` |
| エージェント向け文書を追加・変更するAWIを起草する前 | `agent-basics:writing-standards/references/agent-documents-additions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-additions.md` |

## 投入条件の判定

1. `元項目`を受け取った要求では、最初に`atk wi show <元項目> --target-repo=<起動時のcwdが属するGit worktreeのroot> --skip-pull`を実行して本文を取得し、根拠資料に加える。
   `投入条件`が`無条件`ではない要求では、その条件を観測可能な判定基準へと具体化し、投入先を読み取り専用で調査する。
   条件が不成立の場合はWIを投入せず、`condition_not_met`および調査対象と結果を`条件判定`として返す。
   判定が不可能な場合や投入先の現状を確認できない場合は、「続行できない理由」を返す。

## 前提と観測の確認

観測した不具合・ユーザーの指摘・エラー・hookの誤動作が起点の場合は、調査に入る前に`agent-basics:bugfix`を起動する。
反映内容と反映先を確定する前に、同スキルの「類似見直し」で破られた不変条件を1文で定め、他のフック・コマンド・条文、既存WIや暫定規範が扱う事象も、同じ条件を破るかで対象集合へ対応付ける。
同じ仕組みの箇所は同一WIの対象か登録した独立WIへ対応付け、観測した1件だけに範囲を固定しない。
独立別件の区分、調査の深さ・母集団・終了条件はbugfixの基準にそろえる。

依頼の`再発の経過を問う要求`が`該当`の場合に加え、起草者が逐語本文・趣旨から反復・頻度・悪化・守られなくなった時期への言及と判断した場合は、原因確定前に読込表の2資料を全文読む。
`root-cause-severity.md`「前提の再検討」と`root-cause-scenarios.md`「反復した指摘の原因判定」を適用する。
結論は個別事象の原因と再発の経過に分け、過去対策の有無、未適用・範囲不足・効果不足・別原因の区分、残る原因条件を`## 原因分析`または`## 調査結果と判断根拠`へ記す。

1. 現行の規範・実装と必要な変更履歴で欠陥と修正方針を判断できる場合は、セッション記録の照会を追加しない。
   以下の`確定済みの観測`の記録位置の照会は、過去の実行結果や認可・解釈の確定に記録が必要な場合に適用する。

2. 処理実行時点での対象集合の判定を求める要求は、`drafting-completion-scope.md`「対象集合の仕分け」に従う。
   投入前に完了すべき調査とは、分類軸、到達手段、判定時点、完成条件を確定させることであり、後続の処理側へ委ねた個別の採否判断は実際の処理実行時点で行う。
   委譲先が受け取った`他の要求単位の割当`の単位が、調査の結果確定した根本原因と同一のものを共有していると判明した場合は、その単位と根拠を`続行できない理由`に記載して返す。
   要求を統合するかどうかは委譲元が判断する。
   「投入前に確定すべき事項」および`agent-toolkit:wi-standards`の`references/drafting-completion.md`が投入前の確定を求めている事項は、投入先リポジトリに対する読み取り専用の調査を通じて、計画工程へ先送りせずにエージェント自身で確定させる。
   分割読み込みによって確認可能な未読ファイルや未調査の原因・対象は、安易に作業中断の理由とせず調査を継続する。
   物理的なアクセス不能、権限の不足、調査を尽くしてもなお残るユーザー固有の判断事項、固定要件の誤った帰属など、自力で解消できない条件に限り、明確な根拠を添えて報告する。
   観測された欠陥を起点とする要求の原因、類似見直しの母集団、ならびに再発防止策は、次の該当基準で確定する。
   `agent-basics:bugfix/references/root-cause-severity.md`と`agent-basics:bugfix/references/root-cause-remediation.md`を適用する。
   `agent-basics:bugfix/references/root-cause-actions.md`と`agent-basics:bugfix/references/root-cause-prevention.md`も併用する。
   母集団が指定された投入先リポジトリの外部（他のリポジトリを含む）に及ぶ場合であっても、前述した投入先固定時の処置要否判定と除外記録の手順に従う。
   `委譲元の判断`に含まれる理由についても、その事実上の前提を技術的主張として裏付けを取り、実際の観測結果と異なる場合は観測結果に合わせて修正する。
   ユーザーの方針については記録の所在と該当箇所を確認し、`drafting-completion-policies.md`「方針と判断の記録との比較」に定める優先順位を、現在の逐語要求と`確定した要件`へ適用する。
   既存のWIを前提、制約、代替案の不採用理由、完成条件の根拠として用いる場合は、`origin-and-approval-comments.md`「ユーザー専用の記入欄と是正要求」に定める参照規則に従い、ユーザー専用記入欄を含む有効な要求内容を確定した上で、その要求と原稿の判断との比較を成立性の証拠に残す。
   `確定済みの観測`は、すでに取得済みの記録位置と保存出力を再利用する。
   位置が未取得の委譲先は、`atk run-script session-review-evidence -- --delegation-record parent --tool-calls`を実行して直接の委譲元を照会し、コマンドや入力の特徴、実行時点を手がかりに候補を特定する。
   専念型メインや明示的に指定された別会話の観測結果については、その記録を明示的に指定する。
   取得した`record`・`line`と必要な`result_line`を、同一の記録指定を伴う`--detail <record>:<line>`へ渡して入力と実行結果を確認する。
   解決に失敗した場合は`drafting-workflow-evidence.md`「出所と観測の取得」の再指定手順に従う。
   単なるツール名とタイムスタンプの一致だけでは同一とみなさず、具体的な値と観測条件（実行コマンド、入力、環境変数など）を元の記録や受領内容と突き合わせる。
   同一操作の繰り返しを区別できない場合は、その情報不足を未確定事項として扱う。
   突き合わせが一致した観測結果は、`drafting-completion-decisions.md`「採否と実現方式の確定」における実行結果による裏付けとして利用し、コマンドの再実行を省略する。
   値や条件が一致しない場合、起草中に確認した現物や別の観測結果と食い違いがある場合、または観測後に値を変更する修正が行われていた場合は、再実行して確認する。
   依存関係が指定されている要求では、`states-and-dependencies.md`の基準に基づいて独立した実施と完成判定が可能であるかを確定し、真に必要な依存関係のみを本文およびメタデータに反映する。
   外部の既存挙動の再現を完成条件に含める場合は、`awi-body-external.md`「外部の既存挙動の再現」を起草段階で適用する。
   再現確認を処理側の計画工程へ先送りしない。
   再現できない場合は同節の定めに従い、無制限な再試行や待機によって代用することを避ける。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| WIの調査・起草・保存の共通手順：出所と観測の取得の基準を適用する前 | `drafting-workflow-evidence.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-evidence.md` |
