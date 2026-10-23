# サブエージェント定義ファイルと`<役割名>.parent.md`・`<役割名>.subagent.md`の記述基準

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/sub-agents.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/sub-agents.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/sub-agents.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/delegation/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `delivery-scope.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/delivery-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/sending.md#「委譲プロンプトの構成」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/sending.md#「委譲プロンプトの構成」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/routing.md#「会話を引き継ぐ委譲」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/routing.md#「会話を引き継ぐ委譲」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/base-contract.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/runtime-routing.md#「実行手段」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「実行手段」` |

## `<役割名>.parent.md`と`<役割名>.subagent.md`の組

- `<役割名>.parent.md`の起動を述べる節には、`agents_server`の`start`の各引数（`cwd`・`subagent_md_path`・`extra_params`・`mode`・`model_type`）へ渡す役割固有の値を書く（`subagent_md_path`は役割名）。起動の定型と共通の義務は`agent-toolkit:delegation`のSKILL.mdの「`<役割名>.parent.md`を持つ委譲の起動」が持つため、そちらへ委ねる。全役割に共通する省略時の値（`mode`を指定しないこと、`model_type`省略時にサーバーが工程別設定を使うこと）と、その定型に従う旨も書かない

- 委譲先の恒常手順がClaude Codeの`agents/`配下の定義として既にある場合は、定義本文が`<役割名>.subagent.md`を兼ねるため`<役割名>.parent.md`だけを置く。`起動対象:`行へその定義のスコープ付き識別子を書く。Claude Code用frontmatterと分け、定義本文の`## 入力`のtextコードブロックへ`必須入力名: 対象, 観点`のように入力名をASCIIカンマ区切りで宣言する。agents_server用の`share/*.subagent.md`は後掲のYAMLの`required_inputs`を使う

- 委譲元の起動前の義務に、サーバー、hook、modが起動時に確認・補完・実行する事項（必須入力の確認、`引き継ぎ記録先`の用意、`<役割名>.subagent.md`の所在の特定、委譲元のセッション記録の識別子と所在の特定、定期再確認の装着など）を置かない。実行側が一意に解決できる値を委譲元に組み立てさせると、読込と呼び出しが増え、組み立ての誤りが起動の失敗を招く

## agents_serverのfrontmatter

`<役割名>.subagent.md`は先頭のYAML frontmatterへ起動設定を置く。

```yaml
---
mode: explore
model_type: chores_fast
required_inputs:
  - 対象
optional_inputs:
  - 観点
writes_worktree: false
continuations:
  - instruction: 追加確認
    required: [対象]
---
```

必須キーは`mode`、`model_type`、`required_inputs`、`writes_worktree`とする。
`optional_inputs`と`continuations`の省略時は空配列とする。キー・値・入力名・継続宣言の静的整合性は、全ての配布対象の役割文書を列挙するテストで保証する。
起動時は設定を読み、起動方法と書込主体の判定に必要な`mode`・`writes_worktree`および呼出し入力を確認する。読めない設定は診断して終了する。

| キー | 値と意味 |
| --- | --- |
| `mode` | `delegate`・`explore`・`shell`・`write`のいずれか。後3種は同名modeの軽量な起動条件を使う |
| `model_type` | `execute`・`review`・`review_fast`・`chores`・`chores_fast`・`orchestrate`・`write`。起動時の明示指定による上書きを保つ |
| `required_inputs` | 全受領条件で必須の入力名の配列 |
| `optional_inputs` | 任意または条件付きで必要な入力名の配列。条件は本文の`## 入力`へ書く |
| `writes_worktree` | 起動cwdの作業ツリーへ書く役割は`true`、書込先をその外へ限定した役割は`false` |
| `continuations` | 後述の継続指示の配列 |

必須・任意の入力名は宣言全体で重複させず、空白・バッククォート・読点・全角カンマと先頭・末尾のコロンを含めない。
サーバーは必須欠落と宣言外入力を起動前に拒否する。入力の追加・削除・改名は宣言と本文および呼び元の契約を同じ変更でそろえる。
新しい定型チェックは役割文書の宣言と本文、呼び元の入力・検収の契約の追加だけで起動できる。
共通の読取先は`agent_toolkit/_common/task_documents.py`とし、サーバー・hook・プロンプト生成・レーンの書込判定が同じ宣言を使う。
旧版の入力textブロックと旧役割のモデル・用途対応表は、保存された旧文書と既存sessionの読取互換に限って使う。

作業ツリーへ書く手順、対象プロンプトの任意操作、書込先を限定しない検証環境起動を持つ役割は`writes_worktree: true`とする。
managed-temp・引き継ぎ記録・private-notesだけへ書く役割は`false`とできる。書込先をその範囲へ限定できない役割も`true`とする。
`atk lane integrate`は`false`の担当を書込主体から外す。新しい役割の作成時に書込先を確定し、`writes_worktree`へ明示する。
軽量起動に届く規範とスキルは`delivery-scope.md`の配送範囲表で確かめ、必要な判定基準と手順を役割文書で完結させる。
`explore`は全backendで通常の読取コマンドと読取MCPを使える。対象成果物・委譲入力・ユーザーデータの変更禁止は共通プロンプトが伝え、実行環境が管理するキャッシュ・状態・保存出力と区別する。
同じ版の比較など、役割固有の検収に必要な保存資料は入力契約へ保持する。

`continuations`の各要素は次の入力名の配列と指示を持つ。`instruction`は指示、`required`・`optional`は入力名、`paths`は実在するファイルの絶対パスだけを受ける入力名を持つ。`path_lists`は実在するファイルの絶対パス1件か、それらの空でないJSON配列を受ける入力名、`references`はパスと本文の双方を受ける入力名を持つ。
配列の省略時は空配列、`condition`の省略時は空文字列とする。`paths`・`path_lists`・`references`はその指示が受理する入力に限り、各指示の宣言は1回に限る。
絶対パスは配列の各要素も含め実在するファイルか確かめ、不正な値はその値と再実行形を示して拒否する。本文は役割が定める契約へ渡す。`condition`が条件付きの必須値を定める場合、送信側は対象の工程・履歴から要否を判定する。
サーバーと生成CLIはこの宣言から必須欠落・宣言外入力・パス欄の説明混入をチェックする。生成・配送は`agent-toolkit:delegation`の`references/sending.md`「委譲プロンプトの構成」に従う。

## 起動対象と入力の案内

`<役割名>.parent.md`はH1の直後で最初のH2より前へ`text`のコードブロックを1件置き、1行目を`起動対象:`と半角空白に続けて、直接起動する`<役割名>.subagent.md`のファイル名をASCIIカンマ区切りで並べる。この行が起動関係の基準となる定義である。
呼び元へ統合した契約はH2の節とし、その見出し直後へ同じ起動対象のコードブロックを置く。節内の起動・入力・検収はH3以下へ置き、共有parentと同じ宣言・入力のチェックへ渡せるようにする。
`<役割名>.parent.md`は各委譲先の`required_inputs`の項目名を本文へ逐語で書き、項目ごとに値の確定規則を書く。本文へ置く名称はこの逐語の項目名に限り、箇条書きで列挙する場合は本文を項目名で始め、直後にはコロン・句点・読点・全角丸括弧・空白のいずれかだけを置く（語を追加すると対応付けを誤り、必須入力チェックが欠落を報告して委譲先の起動が失敗する）。

## 返却形式の設計

- 行を省略できる項目の定め方は`agent-toolkit/share/rules-subagent.md`「返却形式の受け渡し」の省略時の値の項に従い、同じ要素は1つの構造だけへ列挙させる

- 判断待ちの通知と続行は同書「返却形式の受け渡し」へ集約し、個別の停止条項を重ねない。調査で確定し得る結論には結果の値を持たせる

## コンテキスト境界

通常のサブエージェントは委譲元と別のコンテキストで動作し、引き継ぐ入力は委譲プロンプトに限る（委譲元の会話履歴、読み込んだファイル、起動したスキルは入力の外にある）。下位のサブエージェントとの間も同じである。ホスト標準のforkは起動時点までの会話履歴も受け取る例外とし、選定条件は`agent-toolkit:delegation`の`references/routing.md`「会話を引き継ぐ委譲」に従う。
fork以外の委譲は文脈が独立する前提で設計し、判定は`agent-toolkit:delegation`の`references/base-contract.md`に従う。
Codexで`agents_server`を使えず組み込み委譲へ代替する場合は、`spawn_agent`へ`fork_turns: "none"`を明示する。
`agents_server`で起動した委譲先が受け取るのは、委譲プロンプトと、`agent-toolkit:delegation`の`references/runtime-routing.md`「実行手段」が定める起動条件で決まるものだけである。

Codexネイティブの`spawn_agent`で起動した委譲先では、`fork_turns`が選ぶ会話履歴、対象worktreeから自動読込される`AGENTS.md`、`SubagentStart` hookが追加する委譲先規範を別々の入力として扱う。会話履歴に規範が含まれることをhook配送の代わりにせず、共通委譲先規範は`rules-subagent.md`を基準となる定義としてCodex hookとagents_serverの通常委譲へ同期する。Claude Code固有の委譲先規範はCodexへ配送せず、軽量な探索・書込・shellの各起動へ共通規範を配送しない契約も維持する。
Codexでforkを選ぶ場合は、`spawn_agent`の`fork_turns`を省略するか`"all"`にする。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/sub-agents.md：コンテキスト境界：2026年9月27日」にある。
役割文書を渡す起動は`agent-toolkit:delegation`のSKILL.md「送信」に従い、会話履歴を引き継がない。
