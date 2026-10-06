# rules-main.codex.md: Codexの主体に適用する規範

本文書はCodexの`AGENTS.md`としてCodexの全主体（メインエージェント、サブエージェントおよび委譲先）へ配送され、`agent-toolkit/rules/`配下の常時規範と同じ拘束力を持つ。
Codex固有の公開能力と常時規範との差分を扱う。「メインエージェントだけに適用する規範」節はCodexのメインエージェントだけへ適用し、他の節はCodexの全主体へ適用する。

## Codex固有の入出力

- ファイルの全文は範囲を指定しない取得で読み、事前の総行数や容量の計測を省く（努力目標。遮断された場合の分割取得で十分である）。hookが全文取得を遮断した場合は、通知が示す連続した行範囲を全て取得したときに全文を読了したものとする。複数ファイルの全文取得は、1セルの出力上限で本文が切り詰められないよう、`02-agent-operations.md`の複数対象をまとめる規定によらず別々のセルで実行する
- 最大出力量を確定できない検索、全件取得、全文取得は、それぞれを単独のセルで実行する
- `functions.exec`から`exec_command`を呼ぶ場合は、返却オブジェクトの`exit_code`と`session_id`を出力本文とともに確認する。`output`だけを転送して終了状態を失わない。`session_id`がある起動は継続セッションを終端まで観測し、終了コードの無い結果を完了として扱わない
- 明示指示がないOfficeファイルは原本を直接変更せず、CSVは消費側の要求に従い、要求が不明な場合はUTF-8 BOM付きで保存する

## Codexのplugin root解決

- agent-toolkitのスキルはplugin marketplaceが導入した実体の`<plugin root>/skills/<スキル名>/SKILL.md`を読む
- `<plugin root>`は`<Codexホーム>/plugins/cache/<marketplaceName>/<name>/<version>`の書式で組み立てる。`marketplaceName`、`name`および`version`には`codex plugin list --json`の`installed`配列から`name`が`agent-toolkit`の要素の値を使う。Codexホームは`CODEX_HOME`が設定済みならその値、未設定なら`~/.codex`とする
- 組み立てた絶対パス配下の`skills/`を確認し、コマンド失敗、対象要素の欠落またはroot不在では固定パスを推測せず委譲元へ差し戻す
- 起点のroot確定はホストのplugin導入情報だけから`SKILL.md`読取前に行い、確定した値を以後も使う（努力目標。同じ導入情報から確定し直しても結果は変わらない）。読取済み`SKILL.md`の絶対パスからplugin rootを再解決する処理は、起点の確定の外で用いる
- 公開サブコマンドがないplugin内部資源は、読取済みのagent-toolkitスキルの絶対パスから現行plugin rootを再解決する
- プロジェクト直下の`.agents/skills/`、`AGENTS.md`がない場合の`CLAUDE.md`および作業に該当する`.claude/rules/`を読む。`~/.codex/agent-toolkit/rules/`は配布元から同期した本文、dotfiles固有スキルはClaude Code側原本へのリンクとして扱う

## Codexホスト契約の適用

Codexのsystem、developer、userの命令階層は、user側の`AGENTS.md`や規範から上書きできない前提として守る。
`AGENTS.md`、プロジェクト規範および常時規範は配送されたroleの範囲で適用し、ホストが上書きを許す範囲では`01-agent.md`「方針が衝突する場合の優先順位」に従う。
公開能力、個別ツールの入出力契約、権限、強制打ち切りも同節の順位の比較から外れる前提として守る。

ツール前の短い`commentary`を求めるホストでは、ツール呼び出しの前に短い`commentary`を送る。コード評価を伴うコマンドでは、処理、読取対象、確認目的を説明する。承認対象では、内容と影響範囲、復元方法を実行前に説明する。

会話圧縮後は、一時対象、処理対象WI、保留、承認、当初目的、確定済み要件および残る完成条件を、対象リポジトリ、private-notesまたはホストの記録原本から再解決する。出所には記録原本の記述を用い、内部要約の言い換えはその代わりから外す。

Codexの`list_agents`が対象を`running`と返す間の待機と、`interrupt_agent`による中断を許す条件は`agent-toolkit:delegation`の`references/runtime-routing.md`「Codex後続操作の共通先行条件」に従う。

Codexの組み込み委譲（`spawn_agent`で起動したthread）の待機はホストの`wait_agent`で終端を観測する。`wait_agent`がある場合は終端を観測してからturnを終え、完了通知だけを提供するホストでは常時規範の再開手順を使う。`agents_server`で起動したsessionの観測は`agent-toolkit:delegation`の`references/runtime-routing.md`「実行手段」に従う。

## メインエージェントだけに適用する規範

### 言語

- 英語のコマンド、識別子、エラーメッセージには、必要に応じて意味または目的を日本語で補足する
- 動詞は標準の活用形で書き、標準的な文法に従う。五段動詞の縮約形とら抜き言葉は書き言葉の形へ直す（努力目標。書き言葉の文体を統一する）

### ユーザー確認と終端

構造化質問と`agent-toolkit:user-confirmation-and-report`の`references/codex-format.md`の固定形式のどちらで質問する場合も、発行の前に同スキル「確認要否の判定」を適用する。目的と認可が確定し、その範囲の内側の手段だけが残る事項は自ら確定して報告する。

ユーザー確認の手段が構造化質問である場合は、実行環境が公開する構造化質問のうち、公開スキーマ、モード制限、用途制限およびホスト命令へ適合する機能を使う。Plan modeで同期型の`request_user_input`を利用できる場合は回答まで待つ。同期型を利用できず非同期型の`request_user_input_async`を利用できる場合は、質問を発行し、後続のユーザーメッセージとして届く実際の回答を元の質問へ対応付ける。発行の成功と選択肢の初期選択は回答または承認として扱わない。適合する構造化質問が無い場合だけ、`agent-toolkit:user-confirmation-and-report`の`references/codex-format.md`を使う。

回答期限を提供しないDefault modeでは、協調モードの確認を前段の手順で提示して回答を待つ。自律モードは回答期限の無い実行環境として`agent-toolkit:user-confirmation-and-report`「手段の選択」の表に従う。権限設定またはauto mode classifierの拒否への確認は、`agent-toolkit:user-confirmation-and-report`が定める例外を適用する。

`atk agents-exit-session`が現在のCodex本体を停止できる場合は、そのツール呼び出しをsession終端とする。停止後の`final`の返却は終端の判定条件から外す。
