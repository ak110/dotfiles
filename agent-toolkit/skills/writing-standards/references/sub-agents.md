# サブエージェント定義ファイルと`<役割名>.parent.md`・`<役割名>.subagent.md`の記述基準

品質基準の正式な提供元は、`agent-basics:writing-standards`の`references/sub-agents.md`である。呼び出し元が指定した条件や読込範囲は、同名の正式な品質資料へそのまま引き継ぐ。節の指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を維持する。

以下はagent-toolkitが所有する運用条件である。呼び出し元の条件と読込範囲に従い、ここに記載された運用節を補う。

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

## `<役割名>.parent.md`と`<役割名>.subagent.md`の構成

- `<役割名>.parent.md`の起動を説明する節には、`agents_server`の`start`引数（`cwd`、`subagent_md_path`、`extra_params`、`mode`、`model_type`）に渡す役割固有の値を記述する（`subagent_md_path`には役割名を指定する）。起動時の定型処理や共通の責務は、`agent-toolkit:delegation`のSKILL.md「`<役割名>.parent.md`を持つ委譲の起動」に集約されているため、そちらに委ねる。全役割に共通するデフォルト値（`mode`を指定しない運用や、`model_type`省略時にサーバーが工程別設定を適用する仕様）や、定型に従う旨の記述は省略する。

- 委譲先の恒常手順がClaude Codeの`agents/`配下に定義済みである場合は、その定義本文が`<役割名>.subagent.md`を兼ねるため、`<役割名>.parent.md`のみを配置する。その際、`起動対象:`の行に該当定義のスコープ付き識別子を記述する。また、Claude Code用のfrontmatterとは別に、定義本文内の`## 入力`に設けたtextコードブロックで`必須入力名: 対象, 観点`のように入力名をASCIIカンマ区切りで宣言する。なお、agents_server向けの`share/*.subagent.md`では後述するYAMLの`required_inputs`を使用する。

- 委譲元の起動前タスクには、サーバーやhook、modが起動時に自動で確認・補完・実行する事項（必須入力の確認、`引き継ぎ記録先`の準備、`<役割名>.subagent.md`の所在特定、委譲元のセッション記録の識別子や所在の特定、定期再確認の付与など）を含めないようにする。実行側で一意に解決できる値を委譲元で組み立てさせると、読み込みや呼び出しのオーバーヘッドが増え、値の組み立て誤りによって起動失敗を招く原因となる。

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

必須キーは`mode`、`model_type`、`required_inputs`、`writes_worktree`の4つである。
`optional_inputs`と`continuations`を省略した場合は、空の配列として扱う。キー、値、入力名、継続宣言の静的な整合性は、配布対象となるすべての役割文書を列挙するテストによって保証する。
起動時には設定を読み込み、起動方法や書き込み主体の判定に必要な`mode`、`writes_worktree`、および呼び出し時の入力を検証する。正しく読み込めない設定が含まれる場合は、診断情報を出力して終了する。

| キー | 値と意味 |
| --- | --- |
| `mode` | `delegate`・`explore`・`shell`・`write`のいずれか。後3種は同名modeの軽量な起動条件を使う |
| `model_type` | `execute`・`review`・`review_fast`・`chores`・`chores_fast`・`orchestrate`・`write`。起動時の明示指定による上書きを保つ |
| `required_inputs` | 全受領条件で必須の入力名の配列 |
| `optional_inputs` | 任意または条件付きで必要な入力名の配列。条件は本文の`## 入力`へ書く |
| `writes_worktree` | 起動cwdの作業ツリーへ書く役割は`true`、書込先をその外へ限定した役割は`false` |
| `continuations` | 後述の継続指示の配列 |

必須入力と任意入力の名前は宣言全体で重複を避け、空白文字、バッククォート、読点、全角カンマ、先頭・末尾のコロンを含めないようにする。
サーバーは、必須入力の不足や未宣言の入力が存在する場合、起動前に処理を拒否する。入力項目の追加・削除・名称変更する際は、宣言、本文、ならびに呼び出し元の契約を同一の変更で整合させる。
新しい定型チェックは、役割文書の宣言と本文、および呼び出し元の入力・検収契約を追加するだけで起動可能になる。
共通の読み取り処理は`agent_toolkit/_common/task_documents.py`に集約し、サーバー、hook、プロンプト生成、レーンの書き込み判定が同一の宣言定義を共有する。
旧バージョンの入力textブロックや、古い役割のモデル・用途対応表は、保存済みの過去の文書や既存セッションとの読み取り互換性を維持する目的でのみ利用する。

作業ツリーへの書き込み手順を含む場合や、対象プロンプトの自由な操作、書き込み先を限定しない検証環境の起動を伴う役割には、`writes_worktree: true`を指定する。
書き込み先をmanaged-temp、引き継ぎ記録、private-notesのみに限定できる役割は`false`に設定できる。書き込み先をこれらの範囲に限定できない役割は`true`とする。
`atk lane integrate`は、`false`が設定された担当者を書き込み主体の対象から除外する。新しい役割を作成する際は書き込み先を明確にし、`writes_worktree`に明示的に設定する。
軽量起動時に届く規範やスキルは`delivery-scope.md`の配送範囲表で確認し、必要な判定基準や手順は役割文書内で完結するように記述する。
`explore`モードでは、すべてのバックエンドで通常の読み取りコマンドや読み取り用MCPを利用できる。対象成果物、委譲入力、ユーザーデータの変更禁止は共通プロンプト側から指示し、実行環境が管理するキャッシュ、内部状態、保存出力とは区別して扱う。
同一バージョンの比較など、役割固有の検収に必要な保存資料は入力契約の中に保持する。

`continuations`の各要素には、以下の入力名配列および指示を定義する。`instruction`には指示内容を記述し、`required`や`optional`には入力名を指定する。`paths`には実在するファイルの絶対パスのみを受け付ける入力名を指定する。`path_lists`には実在するファイルの絶対パス1件、またはそれらを含む空でないJSON配列を受け付ける入力名を指定し、`references`にはファイルパスとテキスト本文の双方を受け付ける入力名を指定する。
各配列を省略した場合は空配列、`condition`を省略した場合は空文字列として処理する。`paths`、`path_lists`、`references`は該当指示が受け付ける入力のみを対象とし、指示ごとの宣言は1回に限る。
絶対パスについては、配列内の各要素を含めてファイルが実在するかどうかを検証し、不正な値が含まれる場合はその値と再実行用の形式を明示してリクエストを拒否する。本文データは役割ごとに定められた契約に従って引き渡す。`condition`によって条件付き必須値が定義されている場合、送信側は対象の工程や履歴に基づいて要否を判定する。
サーバーおよび生成CLIは、この宣言に基づいて必須入力の欠落、宣言外の入力、パス欄への説明文の混入などをチェックする。プロンプトの生成と配送の手順は、`agent-toolkit:delegation`の`references/sending.md`「委譲プロンプトの構成」に従う。

## 起動対象と入力の案内

`<役割名>.parent.md`では、H1見出しの直後かつ最初のH2見出しの手前に`text`コードブロックを1つ配置する。その1行目には`起動対象:`と半角スペースに続けて、直接起動する`<役割名>.subagent.md`のファイル名をASCIIカンマ区切りで列挙する。この行が起動関係を定義する基準となる。
呼び出し元へ統合した契約を記述する場合はH2見出しの節とし、見出しの直後に同様の起動対象コードブロックを配置する。節内の起動手順、入力定義、検収基準はH3以下の見出しに配置することで、共有parentと同様に宣言や入力の自動チェックへ通せるようにする。
`<役割名>.parent.md`の本文には、各委譲先の`required_inputs`で定義された項目名をそのまま逐語的に記載し、項目ごとに値の決定ルールを記述する。本文中に記載する名称はこの逐語的な項目名に厳密に一致させ、箇条書きで列挙する際は文頭を項目名から開始し、直後にはコロン、句点、読点、全角丸括弧、空白のいずれかのみを置く。別の単語を付け加えると項目名の対応付けが正しく認識されず、必須入力チェックで欠落と判定されて委譲先の起動に失敗する原因となる。

## 返却形式の設計

- 行を省略可能な項目の定義方法は、`agent-toolkit/share/rules-subagent.md`「返却形式の受け渡し」におけるデフォルト値の規定に従い、同一の要素は単一のデータ構造にのみ列挙させる。

- 完了・続行不能・通知・追加報告は、同書「返却形式の受け渡し」へ集約し、役割文書には固有の成功値・結果欄・工程境界と共通契約との差分だけを置く。同節は軽量起動にも自動配送される。調査によって確定できる結論は、結果としての具体的な値を持たせる。

## コンテキスト境界

通常のサブエージェントは委譲元とは独立した別コンテキストで動作し、引き継がれる入力情報は委譲プロンプトに限定される（委譲元の会話履歴、読み込み済みファイル、起動済みスキルは入力対象に含まれない）。下位のサブエージェントを起動する場合も同様である。ホスト標準のfork機能を利用する場合のみ、起動時点までの会話履歴を引き継ぐ例外として扱い、その選択基準は`agent-toolkit:delegation`の`references/routing.md`「会話を引き継ぐ委譲」に従う。
fork以外の委譲についてはコンテキストが完全に独立する前提で設計し、その判定基準は`agent-toolkit:delegation`の`references/base-contract.md`に準拠する。
Codex環境で`agents_server`が利用できず組み込みの委譲機能で代替する場合は、`spawn_agent`に`fork_turns: "none"`を明示的に指定する。
`agents_server`経由で起動した委譲先が受け取る情報は、委譲プロンプト、および`agent-toolkit:delegation`の`references/runtime-routing.md`「実行手段」が定める起動条件によって決定されるもののみである。

Codexネイティブの`spawn_agent`で起動した委譲先では、`fork_turns`によって選択される会話履歴、対象worktreeから自動読み込みされる`AGENTS.md`、ならびに`SubagentStart` hookによって追加される委譲先規範を、それぞれ個別の入力として区別して扱う。会話履歴に規範が含まれていることをhook配送の代替とせず、共通の委譲先規範は`rules-subagent.md`を原本の定義として、Codex hookとagents_serverの通常委譲へ同期させる。軽量起動には同書の返却契約短節だけを配送し、全文配送と区別する。Claude Code固有の委譲先規範はCodexへ配送しない。
Codexでforkを選択する場合は、`spawn_agent`の`fork_turns`を省略するか`"all"`を指定する。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/sub-agents.md：コンテキスト境界：2026年9月27日」に記載されている。
役割文書を渡して起動する際は、`agent-toolkit:delegation`のSKILL.md「送信」に従い、会話履歴を引き継がずに実行する。
