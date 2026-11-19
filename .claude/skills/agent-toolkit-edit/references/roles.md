# 役割文書の宣言

`agent-toolkit/skills/atk-mode/roles/`の`<役割名>.parent.md`と`<役割名>.subagent.md`、Claude Codeのサブエージェント定義を作る・改める時の、`agents_server`に固有の宣言と接続である。
一般の基準は`agent-basics:writing-standards/references/sub-agents.md`にあり、本書は`agents_server`の引数、自動で補う項目、frontmatter、`roles/`と`## 出力`への接続だけを持つ。
frontmatterのキーと値（`model_type`を含む）、入力名、返却形式は公開契約である。
変える時は、宣言、本文、呼出側の起動・受領の契約を同じ変更でそろえる。

## `<役割名>.parent.md`と`<役割名>.subagent.md`の構成

- `<役割名>.parent.md`の起動の節には、`agents_server`の`start`の引数（`cwd`、`subagent_md_path`、`extra_params`、`mode`、`model_type`）へ渡す役割固有の値を書き、`subagent_md_path`には役割名を指定する。
  全ての役割に共通の起動の処理と省略時の値（`mode`を指定しない運用、`model_type`を省くとサーバーが工程別の設定を適用する仕様）は、共通の定義元が持つ。
  定義元は`agent-toolkit:atk-mode/playbooks/delegation.md`「`<役割名>.parent.md`を持つ委譲の起動」である。
- 委譲先の恒常の手順がClaude Codeの`agents/`に定義済みの時は、`<役割名>.parent.md`だけを置き、`起動対象:`の行にその定義のスコープ付きの識別子を書く。
  入力名は、Claude Code用のfrontmatterとは別に、定義本文の`## 入力`のtextコードブロックで`必須入力名: 対象, 観点`のようにASCIIカンマ区切りで宣言する。
  `roles/*.subagent.md`では、本書「agents_serverのfrontmatter」の`required_inputs`を使う。
- サーバー、hook、modが起動時に自動で確かめ、補い、実行する項目は、委譲元の起動前の作業に入れない。
  該当するのは、必須の入力の確認、`引き継ぎ記録先`の準備、`<役割名>.subagent.md`の所在の特定、委譲元のセッション記録の識別子と所在の特定、定期の再確認の付与などである。

## 返却形式の設計

- 役割固有の返却形式の定義元は、`roles/<役割名>.subagent.md`の`## 出力`である。
- 完了、続行不能、通知、追加の報告の共通の返却契約と、省略できる行の扱いは、`agent-toolkit:atk-mode/playbooks/subagent.md`「返却形式の受け渡し」が持つ。
  同節は軽量の起動にも自動で届く。

## agents_serverのfrontmatter

`<役割名>.subagent.md`は、先頭のYAML frontmatterに起動の設定を置く。

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

必須のキーは`mode`、`model_type`、`required_inputs`、`writes_worktree`の4つであり、`optional_inputs`、`continuations`、`result_heads`を省くと空の配列として扱う。
キー、値、入力名、継続の宣言の静的な整合は、配布する全ての役割文書を列挙するテストが保証する。
起動時には設定を読み、起動方法と書込主体の判定に要る`mode`、`writes_worktree`と、呼び出しの入力を検証し、正しく読めない設定があれば診断を出して終える。

| キー | 値と意味 |
| --- | --- |
| `mode` | `delegate`・`explore`・`shell`・`write`のいずれか。後の3種は同名のmodeの軽量な起動条件を使う |
| `model_type` | `execute`・`review`・`review_fast`・`chores`・`chores_fast`・`orchestrate`・`write`。起動時の明示の指定による上書きを保つ |
| `required_inputs` | 全ての受領条件で必須の入力名の配列 |
| `optional_inputs` | 任意か条件付きで要る入力名の配列。条件は本文の`## 入力`へ書く |
| `writes_worktree` | 起動cwdの作業ツリーへ書く役割は`true`、書込先をその外へ限った役割は`false` |
| `continuations` | 本書「継続の宣言」の継続指示の配列 |
| `result_heads` | 返却形式が共通の状態行（`状態:`）のほかに定める固定行の配列。agents_serverの返却検査は、状態行、この固定行、`待機中:`、区分と理由の2行のどれも無い返却を形式違反として1回回復させる。`:`で終わる値は前方一致、それ以外は行全体の一致で受理する。返却形式を変える役割文書は同じ変更で更新する |

### 入力の宣言

- 必須の入力と任意の入力の名前は宣言の全体で重ねず、空白文字、バッククォート、読点、全角のカンマ、先頭と末尾のコロンを含めない。
- サーバーは、必須の入力の不足か未宣言の入力があれば、起動の前に拒否する。
- モデルの上書きと候補列の直接の指定にも、`agent-toolkit:atk-mode/references/model-settings.md`「工程別モデル設定」の自律の選択の制限と、ユーザーの明示の指定の境界を当てる。
- 入力の項目を足す・削る・改名する時は、宣言、本文、呼出元の契約を同じ変更でそろえる。
  新しい定型のチェックは、役割文書の宣言と本文、呼出元の入力・検収の契約を足すだけで起動できる。
- 共通の読み取りの処理は`agent_toolkit/_common/task_documents.py`に集め、サーバー、hook、プロンプトの生成、レーンの書込の判定が同じ宣言の定義を共有する。
- 旧版の入力のtextブロックと、古い役割のモデル・用途の対応表は、保存済みの過去の文書と既存のセッションとの読み取りの互換のためだけに使う。

### 書込主体の判定

- 作業ツリーへ書く手順を含む役割、対象のプロンプトを自由に操作する役割、書込先を限らない検証環境を起動する役割は`writes_worktree: true`にする。
- 書込先をmanaged-temp、引き継ぎ記録、private-notesだけに限れる役割は`false`にでき、限れない役割は`true`にする。
  新しい役割は書込先を明確にし、`writes_worktree`を明示する。
- `atk lane integrate`は、`false`の担当を書込主体の対象から除く。
- 軽量の起動で届く規範とスキルは`agent-toolkit:atk-mode/references/agent-documents.md`の配送範囲の表で確かめ、要る判定基準と手順は役割文書の中で完結させる。
- `explore`のmodeでは、全てのバックエンドで通常の読み取りのコマンドと読み取り用のMCPを使える。
  対象の成果物、委譲の入力、ユーザーデータの変更の禁止は共通のプロンプトから指示し、実行環境が管理するキャッシュ、内部状態、保存出力とは区別する。
- 同じ版の比較など、役割固有の検収に要る保存資料は入力の契約の中に保つ。

### 継続の宣言

`continuations`の各要素は、指示の内容を書く`instruction`と、入力名の配列`required`、`optional`、`paths`、`path_lists`、`references`、条件の`condition`を持つ。

- `paths`は実在するファイルの絶対パスだけを受ける入力名、`path_lists`は実在するファイルの絶対パス1件かそれらを含む空でないJSON配列を受ける入力名、`references`はファイルのパスとテキストの本文の両方を受ける入力名である。
- 各配列を省くと空の配列、`condition`を省くと空文字列として扱う。
  `paths`、`path_lists`、`references`はその指示が受ける入力だけを対象にし、指示ごとに1回だけ宣言する。
- 絶対パスは配列の各要素を含めて実在を確かめ、不正な値があればその値と再実行の形式を示して拒否する。
  本文のデータは役割ごとの契約で渡す。
- `condition`で条件付きの必須値を定めた時は、送る側が対象の工程と履歴から要否を判定する。
- サーバーと生成CLIは、この宣言から、必須の入力の欠落、宣言外の入力、パスの欄への説明文の混入などを検査する。
  プロンプトの生成と配送は`agent-toolkit:atk-mode/references/delegation-prompt.md`「委譲プロンプトの構成」に従う。

## 起動対象と入力の案内

- `<役割名>.parent.md`は、H1の直後で最初のH2の手前に`text`のコードブロックを1つ置く。
  その1行目に`起動対象:`と半角スペースに続けて、直接起動する`<役割名>.subagent.md`のファイル名をASCIIカンマ区切りで並べる。
  この行が起動関係を定義する基準になる。
- 呼出元へ統合した契約を書く時はH2の節とし、見出しの直後に同じ起動対象のコードブロックを置く。
  節の中の起動手順、入力の定義、検収基準をH3以下に置き、共有のparentと同じく宣言と入力の自動チェックへ通す。
- 共有の`<役割名>.parent.md`には、入力専用のH2の節（`入力`か`渡す入力`）を1つ設ける。
  節の中の`- 必須入力名:`の行に、各委譲先の`required_inputs`と一致する名前をバッククォートで囲んで並べ、必須、条件付き、任意の各入力の値の決め方を書く（詳細な生成規則は既存の節を参照してよい）。
- 箇条書きで並べる時は文頭を項目名から始め、直後にはコロン、句点、読点、全角の丸括弧、空白のどれかだけを置く。
  別の単語を足すと項目名の対応付けが認識されず、必須入力のチェックで欠落と判定されて委譲先の起動に失敗する。
