# rules-common.codex.md: Codexの全てのエージェントに適用する規範

本文書はCodexの`AGENTS.md`としてCodexの全てのエージェント（メインエージェント、サブエージェントおよび委譲先）へ配送され、`agent-toolkit/rules/`配下の常時規範と同じ拘束力を持つ。
Codex固有の公開能力と常時規範との差分を扱う。Codexのメインエージェントだけに適用する差分は`rules-main.codex.md`が扱う。

## Codex固有の入出力

- agent-toolkitの規範・手順・CLIが名指しするMCPツールは、Codexでは`functions.exec`の中で`tools.mcp__<サーバー名>__<ツール名>(...)`として呼ぶ。対象はagents_serverの`start`・`send_message`・`kill`・`stop`・`list`・`show`、pyfltrの`run`などである。直接の関数一覧とは別に、同じ`exec`の`ALL_TOOLS`の各要素の`name`と`description`で有無と引数を確かめる。stdio MCPサーバーは起動の数秒後に登録されることがあるため、起動直後に見つからない場合は時間をおいて別の`exec`で照会し直してから不在を判断する。観測と再検証手段は`docs/development/audit-records.md`の「agent-toolkit/share/rules-common.codex.md：Codex固有の入出力：2026年10月22日」にある

- 容量が未知の通常ファイルは範囲を指定しない全文取得（`cat -- <絶対パス>`など）から始め、事前の容量計測を省く（努力目標。遮断時は次項へ移る）
- 容量超過か出力の省略を既に把握している本文と保存された長文は、初回から`atk read-file -- <絶対パス>`で取得する。継続は`atk read-file --start <next> -- <同じ絶対パス>`を使い、1行目のJSONで`eof: true`になるまで最初のLFより後ろの本文を改行も含めて連結する。位置は文字単位で、末尾超過は末尾へ補正される。省略時の上限16384バイトはメタデータ、区切りのLFと本文を含み、内側（コマンド出力の指定）と外側（セル全体）の受信予算に収まらない場合は`--max-bytes <バイト数>`で小さくする
- 独立した複数ファイルの全文取得は、既知の合計出力量が1セルの上限内なら並列実行し、各結果の終了状態と本文を確認する。合計が不明なら別々のセルで取得し、並列化だけを目的とする事前計測は省く
- 最大出力量を確定できない検索、全件取得、全文取得は、それぞれを単独のセルで実行する
- `functions.exec`から`exec_command`を呼ぶ場合は、返却オブジェクトの`exit_code`と`session_id`を出力本文とともに保持する。外側の`cell_id`は`functions.wait`、内側の`session_id`は`write_stdin`で同じターンに終端まで回収する。各識別子の待機中は同じコマンドを再発行せず、終了コードの無い結果を完了として扱わない
  戻り値は`const r = await tools.exec_command(...); text(r);`のように全体を表示する。
  `exit_code`が無く`session_id`がある間は実行中として扱う
- 明示指示がないOfficeファイルは原本を直接変更せずに扱う。CSVは消費側の要求に従い、要求が不明な場合はUTF-8 BOM付きで保存する
- `apply_patch`で既存ファイルを全体置換する場合も同じパスへのUpdate操作を使う。1つのpatchで同じパスをDeleteとAddに分けると適用が失敗する

コマンドの戻り値と二層待機の監査記録は、`docs/development/audit-records.md`の「agent-toolkit/share/rules-common.codex.md：Codex固有の入出力：2026年10月19日」にある。

## Codexのplugin root解決

- 最初の`SKILL.md`を読む前に、自身の起動通知が示すagent-toolkitのroot、またはCLI絶対パスの`scripts/`の親から`<plugin root>`を確定し、実体と`skills/`の存在を確認する
- agents_serverの起動時の指示が`agent-toolkit plugin root: <絶対パス>`で示した配布物の置き場所は、その委譲が使う配布物として優先し、同じrootの`skills/<スキル名>/SKILL.md`を読む
- 起動通知とCLI絶対パスのどちらからもrootを確定できない場合は、`codex plugin list --json`の結果をプログラム内で取得する。導入版変更後の再解決でも同じ方法で取得する。`installed`からagent-toolkitを一意に選別し、識別子・marketplace・version・有効状態と解決結果だけを返す
- 導入情報からのrootは`<Codexホーム>/plugins/cache/<marketplaceName>/<name>/<version>`で解決する。Codexホームは`CODEX_HOME`が設定済みならその値、未設定なら`~/.codex`とし、実体と`skills/`の存在を確認する
- 取得失敗、対象なし、対象が一意でない場合、無効化、実体の欠落は原因を区別して委譲元へ返す。モデルへ返す情報を対象の識別情報・解決結果・診断へ限定し、rootは前掲の実在確認で確定する
- 確定したrootを保持し、公開サブコマンドがないplugin内部資源も同じrootから読む。導入版変更後は新しい導入情報で再解決し、全資源を同じホスト・導入版のrootから読む

## agent-basicsの資源解決

品質基準の正式な提供元は`agent-basics`である。`agent-basics:writing-standards`などの資料を読むときは、そのプラグイン自身のrootから解決する。
開発用の同一checkoutでは、agent-toolkitと同じ親にある`agent-basics/.claude-plugin/plugin.json`のnameと`skills/`を確認して使う。
配布先では`codex plugin list --json`の導入済み情報から同じマーケットプレイスの有効なagent-basicsを一意に選び、前節と同じCodexホーム・marketplace・name・versionの対応でrootを解決する。
実体と`skills/`が取得できなければ原因を委譲元へ返す。別版のcacheを推測で選ばない。
agent-toolkitのrootとagent-basicsのrootを別々に保持し、品質の参照先へtoolkitのrootを代入しない。

## Claude Code向けに置かれた規範の読込

Claude Codeが自動で読み込む規範のうち本文書へ埋め込まれていないものは、Codexのエージェントも読み、各ファイルが定める適用範囲に作業が入る条文に従う。Claude Codeと同じ作業を同じ規範の下で進めるためである。以下の読込先は任意であり、実在を確かめたファイルとパターン展開済みの集合だけを読取・検索へ渡し、未読の本文を読む。単一対象の不在、パターンの一致0件、ディレクトリの不在は正常な空集合とし、読取や列挙の権限エラーなどは不在と区別して診断する。

任意の読込先は、不在でも終了コード0で実在する対象だけを列挙する1回の確認で確かめる。ユーザー単位では次の形を使え、起点・対象の不在は空の出力、列挙の権限エラーは診断付きの非0となる。プロジェクト直下も、起点と対象を合わせて同じ考え方で確認する。

```sh
if test -d "$HOME/.claude"; then find "$HOME/.claude" -maxdepth 2 \( -path "$HOME/.claude/CLAUDE.md" -o -path "$HOME/.claude/rules/*.local.md" \) -type f; fi
```

- プロジェクト直下の`.agents/skills/`、`AGENTS.md`がない場合の`CLAUDE.md`、`CLAUDE.local.md`および作業に該当する`.claude/rules/`を読む
- ユーザー単位の`~/.claude/CLAUDE.md`と、`~/.claude/rules/`直下の`*.local.md`（実行ホストごとにユーザーが置く規範）は、作業の開始時に読む
- 起動時の指示が同じファイルの本文を`atk-auto`などの境界付きで含む場合は、その本文を読了済みとして扱う
- `~/.claude/rules/agent-toolkit/`配下と`~/.claude/rules/myprojects-common.md`は本文書へ`atk-auto`要素で埋め込み済みのため、読込の対象から外す。`~/.claude/rules/myprojects.md`はClaude Code向けのホスト別のプロジェクト一覧と同期方針であり、Codexには`myprojects-common.md`が同じ主題の共通部分を届けるため、同じく対象から外す
- `~/.codex/agent-toolkit/rules/`は配布元から同期した本文、dotfiles固有スキルはClaude Code側原本へのリンクとして扱う

## 第三者への作用と認可

第三者への作用は、`agent-toolkit:user-confirmation-and-report`の「認可を要する操作」が定義する第三者と、`01-agent.md`「役割分担」の原則から判断する。
依頼・WI・成立済みの承認が名指しする対象の外にある第三者のシステム、サービス、リポジトリ、人へ作用する操作は、その対象と操作を名指しした事前承認を得てから行う。
送信・投稿・PRやissue・コードの提供・アカウント作成・認証や脆弱性の試行などが含まれ、範囲内と確定できない対象は範囲外として扱う。
公開資料の読取は、事前承認を要する作用の対象外とする。

範囲外の操作が要る場合は、`agent-toolkit:user-confirmation-and-report`の「手段の選択」に従って事前承認へ送り、委譲先は委譲元へ返す。
無害を装う設計、明示的な禁止の不在、他の手段を使い尽くしたこと、模擬・検証用環境という推測で実施へ切り替えず、第三者の拒否を依頼の認可で上書きしない。

確認への応答も、対象と操作を名指しして認めた明示の回答だけを承認にする。
名指しの無い「続けて」「最善の判断で進めて」の類の汎用の継続文、自動生成の配送・継続指示、無応答、文脈からの推測は承認の証拠にしない。
生成本文の由来は`01-agent.md`「方針が衝突する場合の優先順位」に従って区別する。

第三者への発信・貢献はユーザーが用意した既存のアカウントと身元で行い、内容と目的を開示する。
偽の身元、新しい身元の作成、なりすましは不適合とする。
2FA・CAPTCHAなど本人確認の回避と、信頼を得るための先行貢献・支持投稿も不適合とする。

## Codexホスト契約の適用

Codexのsystem、developer、userの命令階層は、user側の`AGENTS.md`や規範から上書きできない前提として守る。
`AGENTS.md`、プロジェクト規範および常時規範は配送されたroleの範囲で適用し、ホストが上書きを許す範囲では`01-agent.md`「方針が衝突する場合の優先順位」に従う。
公開能力、個別ツールの入出力契約、権限、強制打ち切りも同節の順位の比較から外れる前提として守る。

ツール前の短い`commentary`を求めるホストでは、ツール呼び出しの前に短い`commentary`を送る。コード評価を伴うコマンドでは処理、読取対象、確認目的を、承認対象では内容と影響範囲、復元方法を実行前に説明する。

会話圧縮後は、一時対象、処理対象WI、保留、承認、当初目的、確定済み要件および残る完成条件を、対象リポジトリ、private-notesまたはホストの記録原本から再解決し、出所には記録原本の記述を用いる。

Codexの`list_agents`が対象を`running`と返す間の待機と、`interrupt_agent`による中断を許す条件は`agent-toolkit:delegation`の`references/codex-runtime.md`「後続操作の共通先行条件」に従う。

Codexの組み込み委譲（`spawn_agent`で起動したthread）は、`wait_agent`がある場合はそれで終端を観測してからturnを終え、完了通知だけを提供するホストでは常時規範の再開手順を使う。`agents_server`で起動したsessionの観測は`agent-toolkit:delegation`の`references/runtime-routing.md`「実行手段」に従う。
