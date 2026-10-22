# 委譲プロンプトの送信

本書は`agent-toolkit:delegation`「送信」の細則として、委譲プロンプトの構成、起動時の確認、権限と成果物の取り決め、未検証情報の扱いおよび判断材料の渡し方を定める。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「送信」` | 判断 | `section:agent-toolkit:*/skills/delegation/SKILL.md#「送信」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/writing.md#「文章の共通規定」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/writing.md#「文章の共通規定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#%E3%80%8C%60%3C%E5%BD%B9%E5%89%B2%E5%90%8D%3E.parent.md%60%E3%82%92%E6%8C%81%E3%81%A4%E5%A7%94%E8%AD%B2%E3%81%AE%E8%B5%B7%E5%8B%95%E3%80%8D` | 判断 | `section:agent-toolkit:*/skills/delegation/SKILL.md#%E3%80%8C%60%3C%E5%BD%B9%E5%89%B2%E5%90%8D%3E.parent.md%60%E3%82%92%E6%8C%81%E3%81%A4%E5%A7%94%E8%AD%B2%E3%81%AE%E8%B5%B7%E5%8B%95%E3%80%8D` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `base-contract.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/investigation.md#「根拠と主張の一致」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/investigation.md#「根拠と主張の一致」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-standards/references/reviewee.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/references/reviewee.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/sub-agents.md#「agents_serverのfrontmatter」「起動対象と入力の案内」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/sub-agents.md#「agents_serverのfrontmatter」「起動対象と入力の案内」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `waiting-and-monitoring.md#「待機区間の構成」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md#「待機区間の構成」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/investigation.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/investigation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |

## 委譲プロンプトの構成

分量の判定は`agent-toolkit:writing-standards`の`references/writing.md`「文章の共通規定」に従う。
役割指定の初回は`start(subagent_md_path=..., extra_params=...)`、定型継続は`send_message(session_id=..., instruction=..., extra_params=...)`から本文を生成する。
受理する入力名は役割文書のfrontmatter、条件と値の意味は本文の`## 入力`が所有する。
継続では保持済みの初回値を省き、今回の指示に必要な制御値と更新情報を渡す。

組み込み委譲では、要求JSONをUTF-8で保存し、`atk run-script delegation-prompt -- --input <要求JSONの絶対パス>`が返すJSONの`prompt`をそのまま使う。
初回の要求は`kind: initial`、`subagent_md_path`と`extra_params`を持つ。
継続の要求は`kind: continuation`、`instruction`と`extra_params`、または自由本文の`prompt`を持つ。
保持情報は登録済みの`session_id`、既存の起動・セッション記録JSONを指す`history_file`、履歴を持たない場合は`subagent_md_path`から解決する。
`history_file`の既存記録は`prompt`と`followup_prompts`を保持するJSONオブジェクトを使う。
生成CLIは本文の生成・検証だけを行い、起動と配送は呼び出し元が行う。

起動前に、委譲する独立した作業、読取専用か書込可能か、完了条件と受領方法を確定する。
同じ母集団を複数の委譲先へ分割する場合は、全要素と担当の対応表を作成し、未割当と重複が0件であることを集合演算で確かめる（主題単位の分割でも要素単位の対応で判定する）。同じ対象を独立した系統で意図的に確認する作業は重複の判定から除く。

委譲プロンプトは次の2要素だけで構成する。地の文、手順の再掲、権限の説明は委譲プロンプトの外へ置く。

- 1行目: `<委譲先の<役割名>.subagent.mdの絶対パス>の手順を実行せよ。`。専用の`<役割名>.subagent.md`もagent定義も渡さない委譲では、委譲先への命令1文に置き換える
- 2行目以降: 必須入力を項目ごとに列挙する

列挙の規則は次のとおりとする。

- 項目名: 役割文書を持つ委譲は、共有する`<役割名>.parent.md`または単一呼び元へ統合した起動契約から取り、`agent-toolkit:delegation`「`<役割名>.parent.md`を持つ委譲の起動」に従う。項目名は起動契約のまま渡し、サーバーが役割文書の宣言に合うか確かめる。委譲元は起動準備のために役割定義の本文を取得しない。役割文書を持たない委譲は、委譲元の手順が項目名を定める
- 値の書式: 1行に収まる値は`<項目名>: <値>`。改行を含む値と、`<役割名>.subagent.md`が構造を定める値は、`<項目名>:`だけの行に続けて各行（空行を含む）へ半角空白2字を加えて置く。字下げの無い行でその値が終わり、委譲先は2字を除いた文字列を値とする
- 列挙する値: `<役割名>.subagent.md`を指定する起動では、その文書が宣言した入力だけを列挙する。それ以外は`references/base-contract.md`の入力の適格性で選ぶ。汎用委譲では`通常完了の報告様式`を必須入力へ含める。新しい項目が必要になった場合は役割文書のfrontmatterと本文の入力契約へ加え、委譲プロンプトには項目名と値だけを置く
- 省く値: `start`の`cwd`、その`cwd`からGitで解決できる値、`<役割名>.subagent.md`が定める権限と入力、他の値から一意に導出できる値、呼び出し側CLIが内部で導出する派生パス、`<役割名>.subagent.md`が定める省略時の値と一致する行
- 値へ書かない内容: 宣言済みの欄の値はパス、識別子、実行時に確定した事実に限る（結論の文章を運ぶ欄は`<役割名>.parent.md`が値の種類を定める）。委譲先の文書と規範が定める手順、権限、検証方法、返却形式、および背景説明、経緯、判定に至る過程、委譲元の推測は値の外へ置く。委譲先の独立した判断を先回りして狭めないためである
- 未検証の値: 自ら裏付けていない値には「未検証情報の取り扱い」の`未検証`を付し、着手前の裏取りを求める。他の主体の報告・記録・出力から得た値は、`agent-toolkit:writing-standards`の`agent-basics:writing-standards/references/investigation.md`「根拠と主張の一致」の出所へ帰属させる主張に従う。出所が明示しない部分を補った値を自ら裏付けていない値として扱う
- 完了条件・既知の事実・報告様式: `<役割名>.subagent.md`、agent定義、基準となる記録のいずれかが持つものはその記録を参照させ、持たないものだけを書く

基準となる記録で内容が一意に定まる場合は、絶対パスと対象IDだけを置き、手順、品質規範、過去応答、合意事項、調査済み事実、完了条件は転記せず記録を参照させる。複数の委譲先が使う共通資料に基準となる記録が無い場合は、反復と読取負担を比べて有効ならmanaged-tempへ1回保存し、各委譲プロンプトへ絶対パスを渡す（努力目標）。記録が無い情報は委譲プロンプト内で完結させ、対象を本文だけで一意に特定できるように書く。

自由本文の起動（`start`の`delegate`・`explore`・`write`・`shell`）では委譲先が他の手段で得られない情報（対象、目的、確定済みの判断材料、完了条件、返却形式）を書く。サーバーは`mode`（`delegate`・`explore`・`write`・`shell`）ごとにplugin rootの`share/agents-server-<mode>.md`を渡す。その内容（読み取り専用、応答言語、担当範囲など）は書き足す対象から外し、迷う場合はその本文で確かめる。

命令の語は対象・権限・完了条件を示し、その範囲で委譲先が技術判断できるように書く。指摘を受けた修正でも、要求と根拠から修正範囲と方法を独立に選べる条件を保つ。

- 委譲先が適用する作成・レビューの規範を定めるスキルが作業手順を定める委譲では、そのスキル名を必須入力へ含める。指摘や改善提案の修正を委譲する場合は`agent-toolkit:review-standards`の`references/reviewee.md`を含める
- 委譲先が自身と同じ役割へ再委譲せず完遂する条件を、`<役割名>.subagent.md`、agent定義、適用規範のいずれも定めない場合は、`再委譲`という項目名の必須入力で渡す（`agents_server`は委譲先のツールを委譲元から制限する入力を持たない）

ユーザーが定性語・程度語（軽微、過剰、十分など）で示した判定基準を、委譲先が適用する判定条文へ具体化して中継する場合は、その要件を未確定として扱い、判定対象、判定主体、判定時点、機械的な除外一覧か現場判断かを具体化案として示す。案の順位がユーザーだけが持つ値で変わる場合は`agent-toolkit:user-confirmation-and-report`「確認要否の判定」に従って確かめてから送り、技術的に決まる場合は確定して送り報告する。ユーザーが判定条件を逐語で指定した場合と、`agent-toolkit/rules/01-agent.md`「ユーザー発話の解釈」により技術判断で確定できる提案は対象外とする。解釈違いが委譲結果を変える場合だけ適用する。

起動前に検知できない認可の不足、前提の齟齬、ユーザー判断は委譲先からの差し戻しで扱う。

## 起動と観測

役割のfrontmatterと呼び元の`起動対象:`の書式は`agent-toolkit:writing-standards`の`references/sub-agents.md`「agents_serverのfrontmatter」「起動対象と入力の案内」に従う。
`start`は共通パーサーから入力と起動設定を読み、必須欠落・宣言外入力・不正な新形式を起動前に診断する。旧宣言行の読取と既存sessionの継続は互換処理で保つ。宣言を確認できない旧文書では従来の警告を返し、モデル工程別model_typeの旧対応が無い役割は起動しない。
PreToolUseフックは役割の実行命令を持つ初回起動を、サーバーと同じ宣言でチェックする。
`send_message`は構造化・自由本文の双方をサーバーで配送前にチェックする。
宣言外の説明、必須欠落、保持済みの初回入力の再掲は、違反箇所と再実行形を確認して直す。
自由本文の新要求の意味上の必要性は送信側が判断し、機械チェックは宣言・定型指示・既存履歴から確定できる範囲へ限る。

- `agents_server`の各ツールの引数、省略時の値、応答項目は起動直前にMCPツールのスキーマで確かめる。返された`session_id`と`send_message`で指示したsessionの観測と破棄の義務はMCP instructionsに従い、観測は`atk agents wait`で行う。`send_message`は配送結果だけを確定し、応答生成の完了を待たない
- 観測が`running`を返した場合と、観測がバックグラウンドタスクへ移された場合は、いずれも観測を試みたものとして扱い、ターンを終えるかは`references/waiting-and-monitoring.md`「待機区間の構成」に従う
- 作業ディレクトリは受領済みの絶対パスを`start`の`cwd`へ渡す。作業用複製では、委譲先が`cwd`の`--git-common-dir`から複製元とlinked worktreeを解決し、編集を自身のworktree内へ限り、git操作を`git -C <worktree絶対パス>`の形で行う。Gitから一意に定まらない除外対象だけを名前付き入力で渡す

## 権限と成果物の取り決め

- 成果物ファイルを生成・更新する委譲では書込主体を明示する。委譲先が書く場合は委譲元が完了報告の受領まで読み取り専用として扱い、委譲元が書く場合は委譲先へ読み取り専用で扱うよう伝える
- 成果物は指定された絶対パスへ直接書かせ、完了報告に絶対パス、実在と分量を示すコマンド（`ls -l`・`wc -l`等）とその結果、集計値、内容要約を求める
- 件数や行数などの集計値は、集計コマンドとその出力を典拠として併記させ、典拠のない値（目視の数え上げや推測）は後続の判断と成果物へ入れない
- 境界が機械的に一意でない多値の定性分類を委譲する場合は、各値を区別する校正例を判断材料に含め、分類から合計や内訳を導く場合は保存した明細を機械的に数えて一致を確かめる（努力目標）
- 委譲範囲を超える不可逆操作の返却は`agent-toolkit/share/rules-subagent.md`「委譲時の厳守事項」が定める。AWI投入は委譲元が明示的に指示した範囲でだけ行わせる
- 対応方針の推奨や実装上の制約を返す調査では、対象リポジトリの方針文書と対象機能の外部仕様文書の該当節（絶対パス）を読ませ、整合判定と衝突箇所を報告させる
- 点検・調査では各指摘の裏付けに用いた手段を報告させる。裏付けを取得できない場合、レビュー指摘管理表と`track`を使う往復では`agent-toolkit:review-standards`のSKILL.mdに従い、それ以外では証拠不足の範囲、得られなかった根拠、必要な検証を返して完了しないよう求める
- 既存の成果物の削除・撤去・簡潔化の候補を返す点検では、候補ごとに、撤去対象が検出する契約違反、代替のテストか成果物がその違反を検出する具体的な入力と期待値、双方の本文を確認した結果を返させる。判断材料の取得で切り詰めか省略を観測した候補は証拠不足として返させ、証拠がそろった候補だけを撤去の判断へ入れる
- 複数の委譲先のツール、権限、モデル、ホスト能力を比較・分類する場合は、全対象へ同一の機械照会を同じ条件で実行する。条件をそろえられない比較は未検証として返させ、委譲先の自己申告を能力の確定に使わない（単一対象の通常のstatus確認や常時収集は対象外）

## 未検証情報の取り扱い

`未検証`は自ら裏付けを取っていない具体値・挙動・制約に付すラベルとする。付す対象の範囲は`agent-toolkit:writing-standards`の`agent-basics:writing-standards/references/investigation.md`「根拠と主張の一致」の出所へ帰属させる主張の定義に従い、本節は委譲での扱いを定める。

- 委譲先が着手時に読む成果物（委譲プロンプト・計画ファイルなど）へ、自ら確認していない具体値や既存実装の挙動・制約を書く場合は`未検証`と明記し、着手前の裏取りを指示する。位置は行番号でなく見出しや識別子で示す（努力目標）
- 委譲した調査の報告を受ける前に原因をユーザーへ伝える場合は、推定と明記する
- 委譲先が実行する手順として外部コマンド・ツールを書く場合の事前確認は`agent-toolkit:writing-standards`の`references/investigation.md`に従う

## 判断材料と一時領域

一時ファイルの置き場所は`agent-toolkit:managed-temp`に従う。判断材料が2万文字（目安）を超える場合はそこへ所有者だけが読み書きできるMarkdownとして保存し、内容が一意に定まるなら絶対パスと対象IDだけを、定まらないなら絶対パスと判断に必要な範囲の要約を渡して、着手時に読ませる。
