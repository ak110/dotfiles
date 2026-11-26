# Codexでの操作

Codexで動くメインと委譲先が、agent-toolkitの手順書をCodexの公開操作で実行するための手順書である。
方針: Codexのホスト契約（命令階層、ツールの入出力、権限）を前提として守り、手順書が求める結果をCodexの操作で得る。
ツールの有無、実行の終了、本文の受信は、公開操作の結果で確かめる（原則3、`agent-basics:writing-standards/references/quality-principles-facts.md`）。
ホストごとの差の一覧は`agent-toolkit:atk-mode/references/hosts.md`にある。

## 読む節

本書を全文読み、表で場面に合う節を選んで適用する。

| 条件 | 読む節 |
| --- | --- |
| 作業の開始、会話の圧縮の後（全主体） | 本書「Codexのplugin root解決」「agent-basicsの資源解決」「Claude Code向けに配置された規範の読込」 |
| MCPツールやコマンドを呼ぶ、資料を読む、ファイルを編集する（全主体） | 本書「Codex固有の入出力」 |
| 命令と規範の関係、文脈の切り替え、`list_agents`の状態、第三者への作用（全主体） | 本書「Codexホスト契約の適用」「第三者への作用と認可」 |
| 手順書がClaude Codeのツール名を書いている（全主体） | 本書「ツール名の読み替え」 |
| ユーザーへの途中報告、質問、`final`、セッションの停止（メイン） | 本書「ユーザー確認と終了」「言語」 |
| 委譲を起動する、委譲や長いコマンドの終了を待つ | 本書「agents_serverの起動」「agents_serverの二層待機」 |

## Codexのplugin root解決

1. 最初の`SKILL.md`を読む前に、`<plugin root>`（agent-toolkitの配布物のルート）を次の順で確定する。
   - `agents_server`の起動の本文にある`agent-toolkit plugin root: <絶対パス>`（その委譲が使う配布物として最優先する）。
   - 自身の起動通知が示すagent-toolkitのルートか、`atk`のCLIの絶対パスにある`scripts/`の親。
   - 保持している値（導入版を変えた後は破棄して解決し直す）。
2. 上のどれでも確定できない時は、既知のmarketplaceに限って導入情報を取得する（`MARKETPLACE`はその環境で確かめた名前）。

   ```sh
   set -o pipefail
   codex plugin list --json --marketplace "$MARKETPLACE" |
     jq '{installed: [.installed[] | select(.name == "agent-toolkit" or .name == "agent-basics") | {pluginId, marketplaceName, name, version, enabled}]}'
   ```

   marketplaceが不明なら、先に`codex plugin marketplace list --json | jq '{marketplaces: [.marketplaces[] | {name}]}'`で列挙する。
   各marketplaceへ同じ照会をし、無効な候補も診断に残したうえで、候補全体からagent-toolkitを一意に特定する。
   ルートは`<Codexホーム>/plugins/cache/<marketplaceName>/<name>/<version>`とし、Codexホームは`CODEX_HOME`、未設定なら`~/.codex`とする。
3. 確定した値は、実体と`skills/`ディレクトリの実在を確かめてから使う。
   取得できない時は識別子・版・有効状態・診断を保全して原因を分類し、不具合なら `agent-basics:bugfix` で認可内の設定修復や更新後に再取得し、結果または未回復の影響・試行・再開条件を委譲元（メインはユーザー）へ伝える。
4. 公開のサブコマンドを持たない内部の資源も同じルートから読み、全ての資源を同じ導入版から読む。

## agent-basicsの資源解決

品質基準の提供元は`agent-basics`であり、`agent-basics:<スキル>`の資料はagent-basics自身のルートから読む。

1. 開発環境の同じチェックアウトでは、agent-toolkitと同じ親ディレクトリの`agent-basics/.claude-plugin/plugin.json`のnameと`skills/`の実在を確かめて使う。
2. 配布先では、本書「Codexのplugin root解決」の限定照会の結果から、同じmarketplaceの有効なagent-basicsを一意に特定して同じ対応でルートを解決し、キャッシュは導入情報が示す版だけを使う。
3. ルートは別々に保持し、実体か `skills/` が欠ければ診断を保全し、既知の導入情報で認可内の修復を試し、読めてから `agent-basics:bugfix` で原因を確かめ、未確認の版は使わず、本人の操作が必要か回復できない分の影響・試行・再開条件を委譲元へ返す。

## Claude Code向けに配置された規範の読込

Codexも、Claude Codeと同じ規範で作業するため、`atk-mode`の`SKILL.md`とClaude Code向けに置かれた規範を読む。
SessionStartの通知は`atk-mode`の起動を求める1行だけで、規範の本文はhookからも`AGENTS.md`からも届かない。

1. 作業の前と会話の圧縮の後に、`<plugin root>/skills/atk-mode/SKILL.md`を全文読む。
   その「作業と手順書」の表から選んだ手順書は、その操作・判断の前に読む。
2. 次の任意の規範のうち、存在するものを全文読み、各条文の適用範囲に従う。
   - 作業の開始時に、ユーザー単位の`~/.claude/CLAUDE.md`と`~/.claude/rules/*.local.md`（ホスト固有の規範）。
   - 作業ディレクトリからファイルシステムのルートまでの各ディレクトリの`CLAUDE.local.md`と、同じディレクトリに`AGENTS.md`が無い場合の`CLAUDE.md`。
   - 対象worktree直下の`.agents/skills/`と、作業内容に該当する`.claude/rules/`。
3. 次は読む対象から除く。
   - `AGENTS.md`へ埋め込み済みの`~/.claude/rules/myprojects-common.md`と、Claude Code向けの一覧と同期方針を持つ`~/.claude/rules/myprojects.md`。
   - 旧い配布の残りである`~/.claude/rules/agent-toolkit/`（廃止したスキルを起動させる指示を含むため）。
4. 不在と一致0件は正常な空集合とし、読み取り・一覧の権限エラーとリンク切れは診断として区別する。
   1つの候補が失敗しても残りを確かめ、全ての候補の後に総合の終了状態を返す。
   リンク先は権限拒否を迂回せず、取得失敗時は所在と診断で原因を確認し、不具合なら `agent-basics:bugfix` で認可内の修復後に再読し、結果または未回復の影響・試行・必要権限を委譲元（メインはユーザー）へ伝える。
5. 会話の圧縮の後はこれらの本文を読み直し、`atk-auto`などの境界タグ付きの全文が今の文脈にある本文だけを読了済みとする。
   圧縮前に配送された記録は、読了の根拠から外す。

## Codex固有の入出力

### MCPツールの呼び出し

1. 手順書やCLIが指定するMCPツール（`agents_server`の`start`・`send_message`・`kill`・`stop`・`list`・`show`、pyfltrの`run`など）を呼ぶ。
   呼ぶ形は、`functions.exec`の中から`tools.mcp__<サーバー名>__<ツール名>(...)`とする。
2. ツールの有無と引数の仕様は、同じ`exec`の中で`ALL_TOOLS`の各要素の`name`と`description`を見て確かめる。
3. stdioのMCPサーバーは起動から登録まで数秒かかる場合があり、起動の直後に見つからない時は少し待って別の`exec`で照会し直す。
   一覧に名前が無いという1回の観測を、接続の切断の証拠として扱う誤りを避ける。

機械処理の例は`../references/operations.md`「出力の保持と観測」に従う。

### コマンドの実行とファイルの編集

1. `functions.exec`の入力には生のJavaScriptだけを渡し、説明は入力の外か`//`のコメントに置く。
   `Script error`の`SyntaxError`で実行されなかった時は、入力の先頭の非コードを除いて発行し直す。
2. `exec_command`の戻り値はオブジェクト全体を`text(r)`へ渡し、本文、診断、`exit_code`、`session_id`を保つ。
3. 外側の`cell_id`は`functions.wait`で、内側の`session_id`は`write_stdin`で、同じターンのうちに終了まで回収する。
   `exit_code`が無く`session_id`がある結果は実行中であり、同じコマンドを発行し直さずに回収を続ける。
4. `exec_command`の初回の`yield_time_ms`は公開の上限30000ms以内とし、コマンド自身にも有限の期限を持たせる。
   長い終了待ちの所有者と回収時間は、本書「agents_serverの二層待機」で選ぶ。
5. `apply_patch`で既存のファイルを全面的に置き換える時も、同じパスへのUpdateを使う（1つのpatchで同じパスをDeleteとAddに分けると適用に失敗する）。
6. 明示の指示が無い限り、Officeファイルは原本を変えずに扱う。
   共有原本の編集依頼だけでは途中状態の同期は認められないため、編集前に`agent-basics:writing-standards/references/quality-principles-constraints.md`を全文読み、「共有Office文書の編集」に従う。
   CSVは利用側の仕様に従う（仕様が不明ならUTF-8のBOM付き）。

### 資料の読取

1. 読み取りには`cat -- <絶対パス>`か`sed -n '<開始行>,<終了行>p' -- <絶対パス>`を、JSONには`jq`を使う。
   hookの対象一覧のプラグイン名は起動通知の実在するルートへ、スキル名は`skills/<名前>/SKILL.md`へ、参照資料はルート直下と`skills/`配下の一意な実在パスへ解決する。
2. シェルを指定しないコマンドがPowerShellで動くなら、全文は`Get-Content -LiteralPath '<絶対パス>' -Encoding utf8 -ErrorAction Stop`で読む。
   行範囲には`| Select-Object -Skip <開始行-1> -First <行数>`を続ける。
3. 本文の初回の取得では、内側の`exec_command`と外側の`functions.exec`の`max_output_tokens`をともに20000と明示する。
   文章主体ならUTF-8のバイト数÷3の切り上げで見積もり、外側の予算には内側の合計とJSONの包絡を含め、超える見積りなら最初から行範囲かセルを分ける。
   nativeの`original token count`が予算を超えた時は、その値以上の枠を両層で確保できる場合だけ1回取り直し、できなければ行範囲へ分ける。
   分割の最後は`sed -n '<開始行>,$p' -- <絶対パス>`で末尾まで取り、1行だけで枠を超える場合は行範囲の分割で解けない制約として扱う。
   読み込み支援のhookが両層の予算と分割範囲を含む実行例を返した場合は、対象と範囲を確かめて使う。
4. 本文を受け取ったかは、実際に受信した範囲を要求と比べて決める。
   外側の`functions.exec`だけで起きた出力の省略は、内側の`exec_command`のPostToolUseでは捕捉されない。
   hookの通知の有無、内側の実行結果、コマンド文字列、終了コードは、本文を受け取った証明から外す。
   省略や退避で欠けた本文は未取得として補い、空かの確認は`wc -c`か`test -s`で行う（`wc -l`は改行の数である）。
5. hookは本文を代わりに届けず、通知の詳細の保存先を返した時はその全文を、参照先の資料の本文とは別に読む。

## Codexホスト契約の適用

1. Codexのsystem、developer、userの命令階層は、`AGENTS.md`と個別の規範から上書きできない前提として守る。
   `AGENTS.md`、プロジェクト固有の規範、agent-toolkitの手順書は、配送されたroleの権限の内側で、ホストが上書きを許す範囲に限って適用する。
   ホストの公開機能、ツールの入出力契約、権限境界、強制打ち切りも、優先順位の比較の外にある前提として守る。
2. `agents_server`のturnに独自の作業予算、時間枠、ツール回数枠があると自分で推定して、担当内で実行できる残工程を止める誤りを避ける。
   実際の強制制限、利用上限、権限拒否は、観測した診断に対応付けて守る（利用上限は`agent-toolkit:atk-mode/references/usage-limit.md`）。
3. ツール実行の前に短い`commentary`を求めるホストでは、ツール呼び出しの直前に1行だけ出す。
   コード評価を伴うコマンドでは実行内容、読む対象、目的を、承認を要するコマンドでは内容、影響範囲、戻し方を実行前に書く。
   この1行は報告ではなく、操作の実況を途中報告として重ねる根拠にもならない（本書「ユーザー確認と終了」）。
4. context windowを切り替える前（枯渇の通知を受けた時を含む）は、`agent-toolkit:atk-mode/references/work-contract.md`「作成と所有」の記録先へ同書「記録する内容」を保存する。
   保存の成功と保存先の読み戻しを確かめてから切り替え、保存済みと未保存の範囲を分ける。
5. 切り替えや圧縮の後に依頼が見当たらない時は、「依頼を確認できない」と返す前に、自分の作業記録と保存した原本から同書の手順で依頼と残工程を復元する。
   復元できない範囲だけを参照した所在と失敗した操作を添えて返し、ホストのbackendの故障原因の確定とは別の作業として扱う。
6. `list_agents`が対象を`running`と返している間の待機と、`interrupt_agent`による中断は、`agent-toolkit:atk-mode/playbooks/delegation-recovery.md`「後続操作の共通先行条件」に従う。
7. 組み込みの委譲（`spawn_agent`で起動したスレッド）は、`wait_agent`が使える環境ではそれで終了を観測してからターンを終える。
   完了通知だけが届く環境では`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`の手順で回収し、`agents_server`のセッションは本書「agents_serverの二層待機」で回収する。

## 第三者への作用と認可

第三者の範囲は`agent-toolkit:atk-mode/playbooks/confirmation.md`「認可を要する操作」の定義に従う。
第三者への作用は取り消せない操作として扱う（原則4、`agent-basics:writing-standards/references/quality-principles-constraints.md`）。

1. 依頼、WI、成立済みの承認が明示した対象の外にいる第三者（システム、外部サービス、外部リポジトリ、個人）への作用は、対象と操作を特定した事前承認を得てから行う。
   該当するのは外部への送信、投稿、PRやIssueの作成、コードの提供、アカウントの作成、認証の試行、脆弱性の検証などで、公開資料の読み取りは外す。
   範囲の内と確定できない対象は範囲外とし、事前承認は`agent-toolkit:atk-mode/references/delivery.md`「手段の選択」で求める（委譲先は委譲元へ報告する）。
2. 実害が無さそうな設計、禁止規定の不在、代替手段の枯渇、検証用や模擬の環境だという推測は、実行の根拠から外す。
   第三者による拒否は、ユーザーの依頼の認可でも上書きできない。
3. 承認は、対象と操作を指定して承諾した明示の回答に限る。
   対象の無い継続指示（「続けて」「最善の判断で進めて」）、自動生成の配送・継続のメッセージ、無応答、文脈からの推測は承認に数えない。
4. 第三者への発信と外部プロジェクトへの貢献は、ユーザーが用意した既存のアカウントと身元で行い、内容と目的を開示する。
   架空の身元、アカウントの無断の作成、なりすまし、本人確認（2FA、CAPTCHAなど）の回避、信頼を得るための事前の貢献や支持表明の投稿は不適合とする。

## ユーザー確認と終了

1. 確認するかは`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」で決め、目的と認可の内側で手段だけが残る事項は自分で確定して報告する。
2. 質問には、公開スキーマ、モードの制限、用途の制限、ホストの命令に合う構造化質問を使う。
   - Codex CLIの`request_user_input`で推奨案を含む問いは送信直前に「確認要否の判定」による一度の見直しを求められ、方針の承認の質問は`agent-toolkit:atk-mode/playbooks/request-intake.md`手順7の決まった形でないと止められる。
   - Plan modeで同期型の`request_user_input`を使える時は、回答まで待つ。
   - 非同期型の`request_user_input_async`では後続のユーザーメッセージで届く回答を元の質問へ対応付け、方針の承認には`agent-toolkit:atk-mode/playbooks/request-intake.md`手順7の非同期用`title`と`options`を使う。
   - 発行の成功と選択肢の初期選択は、回答と承認から外す。
   - 合う構造化質問が無い時だけ、`agent-toolkit:atk-mode/references/choice-construction.md`「質問の形式」の固定形式を使う。
3. 回答期限の無いDefault modeでは、協調モードの確認を前の段の手段で示して回答を待つ。
   自律モードは期限の無い環境として`agent-toolkit:atk-mode/references/delivery.md`「手段の選択」の表に従い、権限拒否への確認は`agent-toolkit:atk-mode/playbooks/permission-denial.md`に従う。
4. 作業中の質問・状況照会への回答と途中報告は`commentary`で届け、認可済みで実行できる元の作業へ同じターンで戻る。
   `commentary`で送る途中報告は、`agent-toolkit:atk-mode/references/delivery.md`「手段の選択」の送る対象に限る。
   異型例として、メインが`commentary`で「次にCIを待つ」のような実況を続けて送ると、判断が必要な報告が実況に埋もれる。
5. 成果作業を終える通常の`final`は、`agent-toolkit:atk-mode/playbooks/report-completion.md`で確定した作業完了報告の本文で届ける。
   `final`の前に原要求の全体と実行できる残工程を突き合わせ、局所の結果（登録、レビュー、公開、一部の調査、計画）だけで完了と扱う誤りを避ける。
   未送達の報告や実行できる残工程があれば`commentary`とツールで続け、全てのツール処理の後に`final`を届けてターンを終える。
   単発の回答だけの依頼、ユーザーの判断や正当な待機、中止・置換、技術的な不成立、委譲先の返却は、それぞれの手順書の形で終える。
6. `atk agents-exit-session`で自分のCodex本体を止める時は、報告用UWIの保存の検収と必要な`commentary`の送達を先に終え、そのツール呼び出しでセッションを終える。

## 言語

- 英語のコマンド、識別子、エラーメッセージには、必要に応じて意味や目的を日本語で補う。
- 動詞は標準の活用形で書き、五段動詞の縮約形やら抜き言葉は書き言葉の形へ直す（努力目標。書き言葉の文体をそろえる）。

## ツール名の読み替え

手順書に出るClaude Codeのツール名は、Codexでは次の操作へ読み替える。
`agents_server`と組み込みの委譲のどちらを使うかは、`agent-toolkit:atk-mode/playbooks/delegation.md`「実行手段」に従う。

| Claude Code | Codex |
| --- | --- |
| `Agent` | `agents_server`の`start`。forkを選ぶ時（`agent-toolkit:atk-mode/playbooks/delegation.md`「会話を引き継ぐ委譲」）だけ`spawn_agent` |
| `SendMessage`・`TaskStop` | 起動手段が返した識別子と継続・中断の操作（`agents_server`は`send_message`・`kill`）。ユーザーが即時停止を求めたら`kill`に`force=true`を渡す。停止は同じ識別子で確かめる |
| `ToolSearch` | 本書「MCPツールの呼び出し」の`ALL_TOOLS`の照会。必須の機能が無ければ差し戻す |
| `Monitor` | 起動手段の状態確認と待機の結果（`agents_server`は`atk agents wait`） |
| `AskUserQuestion` | 本書「ユーザー確認と終了」の構造化質問 |
| `Skill` | descriptionか明示の起動指定で選び、`SKILL.md`を全文読む。出力を隔離する時は`agents_server`を使う |
| `Read`・`Write`・`Edit`、`Bash`・`Grep`・`Glob`、`WebFetch`・`WebSearch` | Codexのネイティブの編集機能（`apply_patch`など）、`exec_command`のシェル、CodexのWeb機能 |
| `EnterPlanMode`・`ExitPlanMode` | `agent-toolkit:atk-mode/playbooks/write-plan.md`の手順 |
| `ScheduleWakeup`・`CronCreate`・`CronList`・`CronDelete` | 公開機能が無ければ、手動の運用かユーザーへの依頼へ切り替える |

組み込みの委譲は、起動に`spawn_agent`、稼働中への入力の追加に`send_message`、待機中か終了後の継続に`followup_task`、終了待ちに`wait_agent`、自分が起動したエージェントの中断に`interrupt_agent`を使う。
forkは`fork_turns`を省略するか`"all"`にし、全履歴を渡す起動は親のモデルとreasoning effortを継ぐ（上書きの指定は受け付けられない）。
forkしない組み込みの代替（`agents_server`を使えない時）は`fork_turns: "none"`を明示し、必要な委譲の入力を依頼文で渡す。
forkしない時も、委譲先が読む`agent-toolkit:atk-mode/playbooks/subagent.md`はSubagentStartの通知が案内し、`AGENTS.md`は自動読み込みで届く。

## agents_serverの起動

1. `agents_server`の`start`はネイティブの委譲とは別の仕組みで、`model_type`からengine、model、effortを解決する（`agent-toolkit:atk-mode/references/model-settings.md`「工程別モデル設定」）。
2. `engine=claude`の指定は`spawn_agent`へ置き換えず、`agents_server`から起動し、候補列を渡す場合は手順1の資料に従って上書きの区分と根拠を付ける。
   指定のengineを実行する手段が無い時は、未完了として返すか、続行できない理由を報告する。
3. 起動の入力（通常の起動の`whole_contract_path`を含む）と依頼文は、`agent-toolkit:atk-mode/playbooks/delegation.md`に従う。
   起動後のturnが可用性のエラーで終わった時は、`agent-toolkit:atk-mode/playbooks/delegation-recovery.md`の引き継ぎ起動に従う。

## agents_serverの二層待機

長い終了待ちは、待つ担当と回収時間を決めてから始める。
待機区間の組み方は`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`「待機区間の構成」に従い、本節はCodexでの回収の操作を定める。

1. 待つ前に、通知で同じ担当へ再開できるかと、待つ担当の入力費用を確かめる。
2. Codexの担当が未起動の読み取り専用コマンドの終了だけを待ち、独立した先行作業が無い時は、`mode: shell`の子へ分ける案と総費用で比べる。
   総費用には起動、引き継ぎ、子の全入出力、検収、再開、非キャッシュ入力、所要時間を含め、子を選んだら結果は完了通知の後に受け取る。
   子へは、対象の識別子（CIならSHAとbaseline）、コマンド自身の有限の期限、終了コードと両出力の保存先を渡す。
3. 起動済みのコマンドは、保持したcell IDとsession IDで元の担当が回収し、委譲のために起動し直す誤りを避ける。
4. 通知による同じ担当の再開の保証が無い最上位と、同じturnでの回収が必要な場合は、次の二層で回収する。
   - `functions.exec`の中で`agents_server`を起動し、`PostToolUse`のhookがMCPの応答を直接観測できない環境では、起動応答の`root_session_id`を`atk agents wait --root-session-id <値>`へ渡す。
   - 待機のCLIへタスク固有のtimeoutは渡さず、CLI自身の待機上限で終わって対象が未終了なら、その結果を確かめてから新しい待機を起動する。
   - 遅れて始まった待機の回収は`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`に従う。
   - 代替起動、中断、巻き取りは、`agent-toolkit:atk-mode/playbooks/delegation-recovery.md`「後続操作の共通先行条件」を満たした時だけ行う。
5. 終了結果だけを待つ例を次に示す。
   `/absolute/path/to/atk`は確かめたCLIの絶対パス、`ROOT_SESSION_ID`は起動応答で保持した値、`CELL_ID`と`SESSION_ID`は各応答の識別子（`SESSION_ID`は整数）へ置き換える。
   `functions.exec`で`atk agents wait`を1回起動し、`Script running with cell ID ...`が返った場合だけ同じ外側のセルを`functions.wait`で回収する。

   ```javascript
   // @exec: {"yield_time_ms": 180000}
   text(await tools.exec_command({
     cmd: "/absolute/path/to/atk agents wait --root-session-id ROOT_SESSION_ID",
     yield_time_ms: 30000
   }));
   ```

   ```json
   {"cell_id":"CELL_ID","yield_time_ms":180000}
   ```

   内側の結果に`exit_code`が無く`session_id`があれば、`functions.exec`から同じコマンドを回収する（early yieldしたら同じ`cell_id`を`functions.wait`へ渡す）。

   ```javascript
   // @exec: {"yield_time_ms": 180000}
   text(await tools.write_stdin({session_id: SESSION_ID, chars: "", yield_time_ms: 180000}));
   ```

6. 回収時間は次のとおりに選ぶ。
   - 180000msは実待機で確かめた回収時間の目安であり、最大値、固定のsleep、実行期限を表す値ではない。
   - 終了結果だけを待つ時と待機状況の報告だけの時は、外側と内側を合わせて長めに回収し、1000msなどの短い回収は使わない。
     内側60000msの待機を外側1000msで包むと、外側の短い周期の回収が続く。
   - 独立作業、受信済みの通知、追加入力を送る対話へ戻る時だけ早くyieldし、短いコマンドは必要な応答時間で回収する。
   - 180000を待機のCLI自身のtimeoutへ写さず、yieldは同じ実行の続きとして扱い、制御が戻るたびに通知と追加入力を処理する。
   - CIの終了確認など他の長いコマンドも、起動コマンドだけを正規の手段へ替えて同じ二層で回収する。
7. 委譲先の成果物の側だけを補助で観測する時は`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`「成果物側の観測」に従い、セッションの稼働は起動手段の状態確認で確かめる。

## 返す報告に含める項目

解決したplugin rootとagent-basicsのルート（解決できない時は分類した原因）、読めなかった規範の所在と診断、回収を終えていない`cell_id`と`session_id`、続行できない理由を返す。
