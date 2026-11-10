# 読込宣言の受理契約：表と取得方法

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| メインの作業契約が未作成で、作成手順を確認する前 | `work-contract.md` | 判断 | `read:agent-toolkit:*/share/work-contract.md` |

## 表と取得方法

読込表は「時点または条件」「全文読む資料」「契機」「配送操作」の各列を持つ。
動的に解決する資料には「資料選択子」を加える。
読み手が判断に使う列と、hookが解析する列の区別は`agent-basics:writing-standards`の`references/read-method.md`が定める。
既存の読込条件を保ち、資料列と配送操作を以下の受理形式で指定する。
読込単位と資料分割は`agent-basics:writing-standards/references/read-method.md`に従う。

- 「契機」には「起動時」「操作」「判断」を指定する。
  起動時に簡潔な選択基準を受け取り、該当する領域を選択したうえで資料を取得し、指定箇所に注目して具体的な判断へ進む。
- 「時点または条件」の自然文は`ReadTrigger.condition`に保持する。
  現在の主体と異なる役割の行は対象外とする。
  「判断」の自然文条件は、配送操作の一致とは別に成立を確認する。
  `read:`・`section:`の一致だけでは宣言元の役割・工程などの自然文条件を確定せず、未確定の行は条件確認の候補に留める。
  `confirmation`に一致する確認ツール呼出しと本文で識別したWI保存種別は、その操作で確かめられる条件だけを確定する。
  確認ツールは`AskUserQuestion`・`request_user_input`・`request_user_input_async`であり、確認本文・選択肢を起草する契機を観測できる。
  他の未確定な自然文条件は確認候補として示す。
  候補を受けた主体は条件を確かめ、成立する資料の全文を判断前に自分で読む。
  候補は確認のための案内として扱い、必読案内済みの記録と再書込の遮断根拠から除外する。
- 「配送操作」には、バッククォートで囲んだ操作の和集合を指定する。
  共通の語彙は`delegation`、`search`、`root-cause-writing`、`managed-temp-create`、`body-writing`、`verification`、`agent-document-writing`、`confirmation`とする。
  既存ファイルの編集には`existing-file-edit`、Claude Code固有の編集には`claude-existing-file-edit`を使う。
  WI保存は`wi-report-save`と`wi-standard-save`で区別する。
  `--body-file`の先頭frontmatterが`run_skill_report: true`なら報告保存、それ以外（本文未指定・読取不能も含む）は通常保存とする。
  汎用の`bash:atk wi add`・`bash:atk wi edit`による保存宣言は拒否する。
- 引数付きの操作には`skill:`、`write:`、`path:`、`dependency:`、`section:`、`tool:`、`bash:`、`role:`、`read:`がある。
  規範資料の取得には`read:<パスglob>`を使う。
  `section:<パスglob>#<見出し指定>`は規範以外の記録類やMakefileを対象とし、規範への指定は拒否する。
  `role:<役割名>`はその役割を受け取る主体を表す。
- `skill:<名前>`の短縮名は宣言元pluginに実在するスキルに限る。
  別pluginのスキルは`skill:agent-basics:bugfix`のように完全名で指定する。
- 「全文読む資料」の列には、コード記法の資料名、Markdownリンク、または`agent-basics:writing-standards/SKILL.md`のような具体的なパスを記述する。
  規範を指す行はファイル単位で記述し、節指定を付けない。
  `same-document`は同じ文書を参照する資料選択子であり、資料列にもそのファイルを指定する。
  規範以外の資料では、単一の見出し、複数の見出し、開始節から終了節までの範囲、および冒頭の指定を区別できる。
  範囲指定では終了節の配下も含めて扱う（受理記法の所在参照）。
- 純粋な互換参照ファイルでは、H1見出しの直後に`<!-- read-source: agent-basics:<skill>/references/<file>.md -->`を置いて正式な資料を明示する。
  元の読込条件を正式な資料へ引き継ぐ。
  運用ルールと品質基準が混在する参照ファイルにはこの標識を置かず、読込表から正式な資料を直接指定する。
- 共通の`<役割名>.parent.md`を持たない役割は、役割文書内の`<!-- parent-read-source: agent-toolkit:/<資料>#<見出し> -->`によって起動・受領契約の定義元を宣言する。
  これは同一の役割を呼び出す全呼出元が共有する固定契約のみを指し、条件付きで参照する詳細資料は定義元文書の読込表に従う。
- 「資料選択子」には`role-contract`、`review-reference`、`external-pytilpack-index`を指定できる。
  ほかに`artifact-standards`、`same-document`、`project-skill`、`input:<入力名>`も指定できる。
  入力が不足している場合や未対応の取得対象がある場合は、診断情報として通知する。

`input:作業契約`は、通常タスクや委譲の公開入力である`whole_contract_path`、または既存の正式な解決結果へ接続する。
起動入力が無いメインは自身のmanaged-temp直下のwork-contract.mdを解決元にする。
未作成の場合は期待パスを示す警告に従い、`work-contract.md`「作成と所有」に基づいて作成し、全文を取得する。
解決した現行の本文を委譲プロンプトに含め、実在するパス、本文のハッシュ値、入力識別子を共通のstartup-documentとして渡す。
受領側は会話圧縮後も同じ入力から再配送する。
`project-skill`は、呼出側が確定させてReadツールや静的な全文読取へ渡した`SKILL.md`の実体を使用する。
`.claude/skills/`や`.agents/skills/`からの取得入力を認識し、シンボリックリンク先の実体を共通の資料識別に利用する。
最初の`SKILL.md`取得を再発行する前に起動時資料を配送し、取得したスキルの宣言を後続の操作へ適用する。
指定された読込条件を保持し、実体の選択を自然文から推測することは避ける。
`resolved-project-skill:<絶対パス>`は選択したスキルの実体を保持し、会話圧縮後も同一の宣言を読み込む。

Antigravity向けの公式生成形式では、同階層の`agent-toolkit-agy`と`agent-basics-agy`を使用し、manifestの論理名を保つ。
長大なSKILLの要約版である`SKILL.md`は、read-sourceによって生成元の全文資料へ接続する。
生成全文資料の相対参照は元のSKILLディレクトリを起点とし、元のSKILLと同一の識別子で読取操作の成功を記録する。
必要範囲への到達は「名指し参照の読込方法」に従い、指定資料の全文を確認する。
ルートパスは、正式な生成配置においてmanifestの論理名と指定名が一致する実体へ解決する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 編集時の注意点:の基準を適用する前 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
