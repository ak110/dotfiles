# 読込表と起動時の配送

エージェント向け文書の読込表と、委譲先の起動の本文へ資料の全文を入れる配送の契約をまとめる。
読込表を書く人と、配送を実装する人が使う資料であり、エージェントが作業中に読む規範からは外している。
読込表の書き方の基準は`agent-basics/skills/writing-standards/references/agent-documents-basics.md`「読込表」にある。
読込表を解析して資料を案内し、資料の受信を追跡したhookは2026年11月20日に撤去した（理由と再導入の条件は削除前の撤去台帳`docs/development/removed-mechanisms-hooks.md`にある）。

## 読込表

読込表はエージェント向け文書の本文に置く2列（「時点または条件」と「全文読む資料」）の表である。
読み手は次の操作や判断の前に条件を確かめ、成立した行の資料を全文読む。
読み手は読込表に加えて、本文の名指しからも資料を選ぶ。
hookと`agents_server`は読込表を解析せず、条件の判定と資料の選択は読み手が担う。

- 起動のたびに成立する条件（起動時、着手時など）の行は、役割文書の読込表へ置かず、本書「起動時の配送」の宣言へ置く。
  無条件に読ませる資料を読込表へ置くと、起動の本文の大きさに表れず、個別の読込として隠れるためである。
  `agent-toolkit/startup_documents_invariant_test.py`が、役割文書の読込表にこの条件の行が無いことを確かめる。
- 読込表は各文書の本文に置き、別ファイルへ分けない。
  別ファイル化の再発は`agent_basics_standalone_invariant_test.py`が検出する。
- 全ての行を機械で確かめる仕組みは置かない。
  試験は特定の規範の読込表の行が条件別に分けた資料を指すことなど、守る必要のある対応だけを確かめる。
  その試験は`agent-toolkit/agent_toolkit/_testing/normative_contract.py`の`read_table_rows`、`read_contract`、`named_document`を使う。

## 起動時の配送

`agents_server`は委譲先の起動の本文へ、担当が判断を始める前に読む資料の全文を入れる。
役割に固有の資料は、軽量の起動も含めて役割文書のfrontmatterの`startup_documents`で宣言する。
共通の初動は委譲先の入口に置く。
品質原則の索引と事実・返却の資料は、入口が示す制約や返却時の基準と重なるため一律には入れない。
作業契約の書き方は委譲元や復元の手順も含むため、委譲先には引き継ぎ記録の新規・継続の扱いと、記録する時に使う節の所在を示す。

- `startup_documents`の要素は資料の参照の文字列か、`document`と`when`を持つ対応表で書く。
  参照は`<plugin名>:<スキル名>/<相対パス>.md`の形とする。
  `when`は宣言済みの入力名と文字列の値の組であり、起動の入力が全ての組と一致する時だけ資料を入れる。
  解析は`agent-toolkit/agent_toolkit/_common/task_documents.py`の`parse_startup_documents`が行い、形式の誤りは起動前に拒否する。
- 読込表の読み方は列名から分かるため、`read-method.md`は一律に入れない。
- 全体作業契約は、起動の入力が示す記録の全文を同じ形式で入れる（`agent-toolkit/agent_toolkit/_agents_server/launch_requests.py`の`whole_contract_reference`）。

### 本文の形式

資料ごとに`kind="startup-document"`の自動挿入要素へ包み、最初の要素の前に受け取った担当への説明を1回だけ置く。
この説明は要素を読んだものとして扱い、同じ版を取り直さないよう求める。
要素の本文は、`資料:`（絶対パス）、`本文識別子:`（本文のSHA-256）、`相対参照の基点:`の各行と、資料の全文から成る。
資料の中の`${CLAUDE_PLUGIN_ROOT}`を、資料を持つ配布物のrootへ置き換える。
`agent-toolkit/agent_toolkit/_agents_server/launch_requests_inputs.py`の`with_startup_documents`が、役割の作業指示と入力の後ろ、参照資料の冒頭へ挿入する。
役割文書の無い依頼文では末尾へ加える。

起動の本文から資料ごとの所在と版を読み取る関数は`startup_documents.delivered_versions`である。
委譲先の作業契約の案内（`agent-toolkit/agent_toolkit/_hooks/work_contract.py`の`delegated_notice`）はこの版を使う。
起動時に入れた全体作業契約の版から原本が更新された時に、更新後の版ごと主体ごとに1回だけ案内し、本文の配送と遮断はしない。

### 資料の解決

`agent-toolkit/agent_toolkit/_agents_server/manager_start.py`の`_startup_documents`が、起動候補ごとのhostとcwdで資料を解決する。
役割の資料は役割文書と同じ配布物（作業ツリーの写しを含む）から解決する。
作業ツリーで役割文書を改訂する担当が、稼働版に無い新設の資料を同じ作業ツリーから受け取れるようにするためである。
全rootを作業ツリーへ切り替える案と、欠けた資料を別版から順に探す案は、通常の導入版の選択を壊し版の混在を隠すため採らない。

- 別のプラグイン（agent-basics）の資料は、隣に置かれた配布物があればそこから解決し、無ければ起動候補のhostの導入一覧（Claude Codeは`claude plugin list --json`）から選んだ導入先から解決する。
  導入一覧は1回の起動の中で共有する。
- Claude Codeの導入一覧から選んだ導入先に資料が欠けていれば、`agent-toolkit/agent_toolkit/_common/plugin_roots.py`の`raise_for_incomplete_installation`がその導入を修復する操作を示して起動前に失敗する。
  導入先の外の欠落は文書の誤りとして診断する。
- 役割の資料を解決できない失敗は、役割の版と対応付けて起動前に返す。
- `agent-toolkit/startup_documents_invariant_test.py`が全ての役割の資料の実在を確かめる。
  起動の本文への挿入と解決の振る舞いは`agent-toolkit/agent_toolkit/_agents_server/startup_documents_test.py`が確かめる。
