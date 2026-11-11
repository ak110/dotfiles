# サブエージェント定義ファイルと`<役割名>.parent.md`・`<役割名>.subagent.md`の記述基準

品質基準の正式な提供元は、`agent-basics:writing-standards/references/sub-agents.md`である。
呼び出し元の読込条件を引き継ぎ、本書の運用条件と条件に該当する正式な資料の全文を読む。
両方に同じ主題があれば、両方の全文を確認する。
範囲の選択と取得済み本文の再利用は`agent-basics:writing-standards/references/read-method.md`に従う。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## `<役割名>.parent.md`と`<役割名>.subagent.md`の構成

- `<役割名>.parent.md`の起動を説明する節には、`agents_server`の`start`引数（`cwd`、`subagent_md_path`、`extra_params`、`mode`、`model_type`）に渡す役割固有の値を記述する（`subagent_md_path`には役割名を指定する）。
  起動時の定型処理や共通の責務は、`agent-toolkit:delegation`のSKILL.md「`<役割名>.parent.md`を持つ委譲の起動」に集約されているため、そちらに委ねる。
  全役割に共通するデフォルト値（`mode`を指定しない運用や、`model_type`省略時にサーバーが工程別設定を適用する仕様）や、定型に従う旨の記述は省略する。

- 委譲先の恒常手順がClaude Codeの`agents/`配下に定義済みである場合は、その定義本文が`<役割名>.subagent.md`を兼ねるため、`<役割名>.parent.md`のみを配置する。
  その際、`起動対象:`の行に該当定義のスコープ付き識別子を記述する。
  また、Claude Code用のfrontmatterとは別に、定義本文内の`## 入力`に設けたtextコードブロックで`必須入力名: 対象, 観点`のように入力名をASCIIカンマ区切りで宣言する。
  なお、agents_server向けの`share/*.subagent.md`では後述するYAMLの`required_inputs`を使用する。

- 委譲元の起動前タスクには、サーバーやhook、modが起動時に自動で確認・補完・実行する事項（必須入力の確認、`引き継ぎ記録先`の準備、`<役割名>.subagent.md`の所在特定、委譲元のセッション記録の識別子や所在の特定、定期再確認の付与など）を含めないようにする。
  実行側で一意に解決できる値を委譲元で組み立てさせると、読み込みや呼び出しのオーバーヘッドが増え、値の組み立て誤りによって起動失敗を招く原因となる。

## 返却形式の設計

役割固有の返却形式は`share/<役割名>.subagent.md`の`## 出力`を定義元とする。
呼出側の既存の起動・受領契約へ必要な部分だけを生成配置し、委譲元の起動準備と形式の検収はその配置先で完結させる。
生成部分は手動で編集せず、定義元から同期する。
内部手順と起動設定は生成対象に含めない。

- 行を省略可能な項目の定義方法は、`agent-toolkit:/share/rules/rules-subagent.md`「返却形式の受け渡し」におけるデフォルト値の規定に従い、同一の要素は単一のデータ構造にのみ列挙させる。

- 完了・続行不能・通知・追加報告は、同書「返却形式の受け渡し」へ集約し、役割文書には固有の成功値・結果欄・工程境界と共通契約との差分だけを置く。
  同節は軽量起動にも自動配送される。
  調査によって確定できる結論は、結果としての具体的な値を持たせる。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| agents_serverのfrontmatter・起動対象と入力の案内に関する判断・作業の前 | `sub-agents-declarations.md` |
| コンテキスト境界に関する判断・作業の前 | `sub-agents-context.md` |
