---
name: delegation
description: >
  作業へ着手する前に担当範囲の一部を委譲先へ分けるかと委譲単位を判定する時
  （計画の起草前、独立した複数の作業単位か長い工程を含む作業の着手前）、
  委譲済みの要求について自ら本文を読む、追加調査する、回答を作成する前、
  サブエージェントまたは委譲先を起動、観測、継続もしくは巻き取るとき、または停滞検知・複数のエージェントの調整が必要な場合に起動し、委譲の共通契約と実行手段ごとの契約を提供する。
user-invocable: false
---

# 委譲

エージェントを起動した側を委譲元と呼ぶ。委譲先は、`Agent`ツールで起動する子エージェント（サブエージェント）と`agents_server`で起動する子セッションの総称である。委譲先がさらに別のエージェントを起動した場合も、起動した側を基準として委譲先と呼ぶ。「孫」や「子孫」という表現は、基準となるエージェントを同一文中で明示する場合に限り用いる。
委譲先が担う用途固有の作業内容は、委譲先自身が読み込む`<役割名>.subagent.md`やagent定義で指定する。

実際に委譲するには、エージェントの起動や終了を受け取る手段と、参照すべき手順資料が必要である。これらが備わっていない環境では、作業の分担案と委譲プロンプトの下書きを作成して返し、実際の起動や待機は該当機能を利用できるClaude CodeまたはCodexで実施する。下書きを作成する際は[文章の品質基準](references/quality-standards.md)に従い、計画上の分担と実際に起動した担当を区別して記述する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/quality-standards.md` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

該当する状況になったら、次の表に示した資料を事前に全文読み込む。同一セッション内ですでに読み終えており、その後に更新がない資料は再読を省略してよい（会話圧縮後は読み直す）。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| Codexで委譲、待機、状態確認または中断へ着手する前（agents_serverのexplore・shellの起動から受領までを除く） | `references/codex-runtime.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/codex-runtime.md` | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `references/base-contract.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract.md` | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `references/mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `references/sending.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/sending.md` | |
| 作業へ着手する前に、担当範囲の一部を委譲先へ分けるかと委譲単位を判定する時（計画の起草前、独立した複数の作業単位か長い工程を含む作業の着手前） | `references/routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` | |
| 呼び出し元の起動契約で手順と必須入力が定められていない委譲を開始する前（explore・shellを除く）と、確定済みの委譲単位の構成を変更する前 | `references/routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` | |
| 委譲先とモデルを選定する時（explore・shell、および役割文書を持ち`model_type`を指定しない起動を除く） | `references/runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` | |
| 元担当の継続・回復・置換・再起動または役割引継ぎを選ぶ前 | `references/runtime-routing.md`の「継続と新規起動」 | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「継続と新規起動」` | |
| Claude Codeで`Agent`ツールの委譲先やバックグラウンドタスクを初回起動する前、定期再確認タスクを自身で設定する前、設定や回収の失敗通知に対処する時、Claude Code固有の手順を確定する時（explore・shellの起動から受領までの操作は除き、本書の同名節に従う） | `references/claude-code-runtime.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime.md` | |
| 完了通知の処理、ならびに待機や停滞の判定を行う時 | `references/waiting-and-monitoring.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md` | |
| `atk agents wait`が終了コード4・8・10、または待機対象0件の非ゼロで終了した時、待機呼び出しがホストの上限により失敗した時、ホストからバックグラウンド待機の停止通知を受信した時、委譲先や自身のツール呼び出しが終了ステータスを返さずに終わった時、`agents_server`のMCPサーバーが再起動した時 | `references/wait-anomalies.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies.md` | |
| 稼働中の委譲先へ追加指示を送る前 | `references/base-contract.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract.md` | |
| 委譲済みの要求について自ら本文を読む、追加調査する、回答を作成する前 | 本書「継続と新規起動」、`references/routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` | |
| 同一worktreeに対して後続または並列の書き込み担当を起動する前（shellを除く。shellは本書「explore・shellの起動」に従う） | `references/mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` | |
| 委譲先からの完了報告を受領して検収する前（explore・shellを除く） | `references/receiving.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/receiving.md` | |
| 委譲先からの完了報告を受領して検収する前（explore・shellを除く） | `references/mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` | |
| 委譲先がプロンプトで`引き継ぎ記録先`を受け取り、最初の記録を書き込む前 | `references/handoff-record.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/handoff-record.md` | |
| 実行工程を委譲する際の受領ルールを確定する時（explore・shellを除く。`起動経路`が`agent-toolkit:process-wi`の場合） | `../process-wi/references/run-lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/run-lanes.md` | |
| 実行工程を委譲する際の受領ルールを確定する時（explore・shellを除く。`起動経路`が`agent-toolkit:write-plan`による直接起動の場合） | `../write-plan/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/write-plan/SKILL.md` | |
| 指摘の採否と修正方針を確定する時 | `agent-basics:review-standards`を起動し、同スキルの`references/reviewee.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` | |
| Claude Codeで長時間待機前の会話圧縮、またはバックグラウンドタスクの起動形を判断する時 | `references/waiting-claude-code.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-claude-code.md` | |
| 停滞の検知と巻き取りを判断する時 | `references/waiting-stalls.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-stalls.md` | |
| agents_serverのexplore・shellを起動する前 | 本書「explore・shellの起動」 | 判断 | `section:agent-toolkit:*/skills/delegation/SKILL.md#「explore・shellの起動」` | |
| 結果本文の保存期間と回収手順を決める前 | `agent-toolkit:managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` | |
| 役割文書を持つ委譲を起動する前 | 呼び出し元の起動・受領契約。共有契約は`${CLAUDE_PLUGIN_ROOT}/share/<役割名>.parent.md`、単一の呼び出し元に統合された契約はその呼び出し元が示す節と条件付きで挙げる資料 | 操作 | `delegation` | `role-contract` |

## 送信

全起動経路の共通返却・エスカレーション契約は`share/rules-subagent.md`「返却形式の受け渡し」が所有し、agents_serverが通常起動へ全文、軽量起動へ自己完結した短節を届ける。呼出元は役割固有の入力と結果だけを補い、形式未指定時は同節の正常結果の形式を用いる。taskの不正な続行不能に対する1回の自動回復と、その後の受領は`references/receiving.md`「返却形式の適合確認」に従う。

定型の委譲内容は、呼び出し元の起動・受領契約と`<役割名>.subagent.md`で定義する。単一の呼び出し元だけで使う契約はその規範内へ統合し、複数の呼び出し元で共有する契約は`<役割名>.parent.md`として配置できる。
評価の観点、判定基準、具体的な手順、返却形式は役割文書に記載する。委譲元は呼び出し元の契約に基づき、対象と実行時の値のみを渡す。自由記述のプロンプトによる起動は、役割文書が存在しない単発の作業に限定して用いる。
役割固有の返却形式は役割文書の`## 出力`が定義を所有し、呼出側の既存の起動・受領契約へ必要な部分だけを生成配置する。
委譲元はその配置先で入力と返却形式を確認し、起動と検収を進める。
委譲元は起動の準備段階で`<役割名>.subagent.md`の本文を読み取らない。起動設定や必須入力の形式チェックはサーバーに任せる。この読取の省略は、ファイル自体の編集のための読取、委譲先自身による読取、完了後の形式確認とは区別する。
規範の変更を適用する委譲先には、作業ツリー原本の絶対パス、適用する節、対象の版を渡し、委譲先自身が判断前に読み込む。同じplugin rootに属する起動契約と役割定義をセットで使用し、すでに稼働しているCLI、フック、MCPが参照している版と作業ツリーの版を区別して扱う。
役割文書を指定する委譲は、委譲元の会話履歴を引き継がない方法で起動する。
Codex環境で`agents_server`が使えず組み込みの委譲で代用する場合は、`spawn_agent`に`fork_turns: "none"`を明示する。
会話履歴を引き継ぐforkを選択できる条件は、`references/routing.md`「会話を引き継ぐ委譲」に従う。

委譲プロンプトは、1行目の命令と必須入力の列挙という2つの要素で構成する。具体的な構成と列挙書式は`references/sending.md`「委譲プロンプトの構成」に従う。プロンプトに含めてよい要素は、`references/base-contract.md`の「入力の適格性と追送」で定める。exploreおよびshellの起動については次節に従う。

## explore・shellの起動

個別の調査やコマンド実行を自身で行うか、explore・shellへ委譲するかは、agents_serverのserver instructionsに記載されている採算の目安に基づいて判断する。`references/routing.md`の全文読込は、作業全体の分割を判定する際に行う。
両モードの起動から受領までに満たすべき必須の要件は本節で定める。括弧内の参照先は詳細情報の所在を示すものであり、実際の読込義務は前述の読込表の指定に従う。

- 全体点検する際は、`references/routing.md`「コンテキスト消費が大きい調査の切り出し」に基づき、公開操作、共通定義、呼び出し元、必要な情報量から、完了可能な担当範囲を定める。調査対象全体の管理と結果の統合は委譲元が担う。
- 起動前に対象、読取・書込の権限、完了条件、返却すべき観測値を確定する。委譲先が読み込む既存ファイルの絶対パスの実在、比較基準、実行する版を確認する。分量の多い調査では読取ツールの範囲指定を渡し、出力の切り詰めや上限到達を網羅性の根拠として扱わない（base-contract.md「起動前の確認」「分離実行の受領物」）。
- 入力は命令1文と必要な値（元の要求、対象、完了条件、返却形式、基準記録）にとどめ、すでに配布済みの制約、経緯、推測、既存記録の転記は含めない。自身で裏付けをとっていない値は未検証情報として明示する。exploreは読取専用として結果を返し、後続の入力には`atk agents wait`が保存した`agent_message_path`を直接指定する（base-contract.md「入力の適格性と追送」、sending.md「委譲プロンプトの構成」「未検証情報の取り扱い」）。
- 各モードの入力項目や機能は、公開されている`start`の説明とスキーマで確認し、`cwd`に作業ディレクトリの絶対パスを渡す。exploreでは`prompt`を、shellでは`command`と`summary_policy`を使用する。スキルの手順を必要とする作業は`task`または`delegate`に委ねる（sending.md「起動と観測」）。
- 作業を分割する場合は`references/routing.md`「要否判定」を用い、モデルを選択する場合は`references/runtime-routing.md`「工程別モデル設定」に従う。明示的な指定がない場合は工程別の設定値を使用し、WIにモデル指定がある場合はそれを優先する。受領時には、指定値、起動候補、観測された実際の値を区別して扱う。
- exploreは読取専用とする。shellの書込先は、生成物も含めて他の担当と重複しない範囲に限定し、同一担当の交代は先行する担当の終了後に実施する。同一のworktreeで書込を分担する場合、コミット、Gitフックの実行、インデックスや履歴の操作、全体のコード整形、自動生成の同期、依存関係の更新、環境構築は、全担当が終了した後に呼び出し元が一括して直列に行う。別ブランチや別worktreeを用いるグループでは、各グループの担当が自身のコミットとGitフックの処理を担い、フックによる更新物も同一コミットに含める。グループ間で重複する生成物は、レーン担当が統合後に原本から同期する。担当ごとのコミットや内容の書き換えを伴う検証が必要な場合、または書込先を事前に限定できない場合は、別のworktreeおよびブランチに分離する（mandatory-rules.md「作業ツリーの保護」）。軽量起動では自動配送されないプロジェクト固有の指示や、コマンドの入力境界、終了状態、標準出力・標準エラー出力の保持を確実に引き渡す（base-contract.md「起動前の確認」、sending.md「権限と成果物の取り決め」）。
- 返却された`session_id`を保持し、`atk agents wait`を用いて終了状態と結果本文を受け取る。待機の実行形式、終了コード、バックグラウンド化、通知後の受領手順は、読込表にある`references/waiting-and-monitoring.md`に従い、ホスト別の待機手順は後述の「軽量起動のホスト別待機」に従う。
- 終了後に対象ファイルの差分、実行結果、診断情報を確認し、返却形式ならびに対象・権限・完了条件との整合性を検収する。件数、不在、網羅性は、省略のない取得結果に基づいて判定し、事実の主張は実物の根拠で裏付ける。受領前の段階で得られた情報のみで完了とみなさず、不足がある場合は同一担当へ差し戻す（receiving.md「返却形式の適合確認」「終了と報告内容の確認」、sending.md「権限と成果物の取り決め」）。

結果本文の再保存は行わず、後続処理が参照する`agent_message_path`や待機実行の保存成果物は、参照が完了するまで保持する。保存期間と回収の手順は`agent-toolkit:managed-temp`に従う。

### 軽量起動のホスト別待機

Codex環境では、`functions.exec`内の起動応答で返された`root_session_id`を保持し、ランタイム間で識別子を引き渡すために`atk agents wait`の`--root-session-id`へ指定する（これは待機対象のセッション集合を指定するオプションとは異なる）。
待機CLIや外側の実行セルの回収、yield中の再発行・代替起動・巻き取りの扱いについては、`references/codex-runtime.md`「agents_serverの二層待機」に従う。
待機CLIに独自の待機対象やタイムアウト値を渡さず、CLI自身が上限に達して`running`を返した後に次の待機コマンドを発行する。

Claude Codeが会話イベントを処理するために読み込むプラグインモジュールをFunction hooks module（以下「mod」）と呼ぶ。Claude Codeのメインエージェントは、最初の`start`の実行時にmodが設定した定期再確認タスクのIDを保持する。modは自身の一覧取得・作成・削除の機能を確認した上でタスクを設定し、すべての待機対象が終了して結果を回収した後に共通プロンプトのタスクを自動回収する。モデルに対して`CronDelete`ツールが公開されているかどうかは、自動回収の条件に含めない。タスク設定の失敗通知を受け取った場合は、記載されている理由と指示に従って対処する。modによる設定通知がなく、`CronList`にも該当タスクが存在しない場合に限り、`CronCreate`・`CronList`・`CronDelete`が利用可能であれば自身でタスクを設定する。

- `atk wait-schedule`を1回実行し、返された`cron`と`prompt`をそのまま`CronCreate`へ渡し、`recurring: true`で1件のタスクを作成する。
- 自身が管理するタスクであることは、プロンプトの1行目にある`<atk-auto source="periodic-recheck" kind="periodic-recheck">`という識別標識と、保持しているタスクIDで確認する。セッションの再開や会話圧縮の後も`CronList`に存在する実タスクと突き合わせ、一意に確認できないタスクの操作や重複作成は避ける。
- 未完了の対象や未回収の結果が残っている間は、同一のタスクを再利用する。共通プロンプトのタスクの通常回収はmodが担当し、追加の義務を持つタスクはセッション終了時に回収する。modが存在しない環境で自身が設定したタスクは、すべての対象が終了した後に保持しているIDを`CronDelete`に渡して削除する。定期プロンプトの実行時には待機CLIが管理する対象を再照会せず、終了の判定はCLIの結果および完了通知に基づいて確定する。
- セッション固有の経過時間に応じた起動義務がある場合は、義務ごとの計測コマンドと判定閾値を共通プロンプトの終了タグの前に追加し、既存のタスクを削除した上で同じ開始標識を用いて再作成する。
- Cronツールが使えない場合や`atk wait-schedule`が失敗した場合は、バックグラウンドタイマーを追加せず、完了通知による待機を継続する。`Agent`ツールのサブエージェントにはタスクを設定せず、agents_serverで起動された独立セッションのメインエージェントは同様の設定条件を適用する（claude-code-runtime.md「待機中の定期再確認と背景転換」）。

## `<役割名>.parent.md`を持つ委譲の起動

呼び出し元の契約で起動手順が定められている委譲は、次の定型手順で起動する。本節の規定は、共有の`<役割名>.parent.md`を用いる場合と、単一の呼び出し元規範に統合された契約を用いる場合の両方に適用する。委譲元は起動前に該当する契約を読み、前提条件、入力値の決定規則、受領と検収の要件に従う。各契約には役割ごとに異なる引数値のみを記述する。

- `agents_server`の`start`を呼び出し、`cwd`に起動契約で定められた作業ディレクトリの絶対パスを渡す。
- `subagent_md_path`に役割名（例: `add-wi`）を渡す。サーバーが自身のplugin root配下にある`share/<役割名>.subagent.md`を解決する。
- `extra_params`に起動契約が指定する入力名と値を渡す。
- `mode`は指定しない。`model_type`は起動契約に値の決定規則がある場合にのみ指定する。省略された場合のmodeやmodel_typeは、サーバーが役割文書のfrontmatterから自動的に解決する。
- `引き継ぎ記録先`の扱いについては、`references/base-contract.md`「引き継ぎ記録先」の規定に従う。
- 起動後は返却された`session_id`を`atk agents wait`で観測する。Claude Code環境では、初回の`start`処理の中でagent-toolkitのmodが定期再確認タスクを設定し、その結果が`start`の返却後に通知される。

`extra_params`に設定する値には、`references/sending.md`「委譲プロンプトの構成」の「値へ書かない内容」と「未検証の値」、ならびに`references/base-contract.md`の`forwarded-user-input`標識と絶対パスの規定を適用する。
必須入力の漏れや未宣言の入力名の拒否、`引き継ぎ記録先`の準備、応答言語の伝達、`<役割名>.subagent.md`の実在確認、`${CLAUDE_PLUGIN_ROOT}`の展開はサーバー側で処理する。サーバーから起動を拒否された場合は、エラー応答に示された項目と指示に従って再起動する。

## 継続と新規起動

元の担当者の回復、置換、再起動、代替起動、役割の引き継ぎを行う際は、事前に`references/codex-runtime.md`「後続操作の共通先行条件」を適用する。識別子の再利用か新規起動かの選択は、`references/runtime-routing.md`「継続と新規起動」に従う。

- 同一の作業を継続し、計画、進捗ログ、保存済みのレビュー指摘管理表のいずれかに状態が記録されている場合は、同じroute、`model_type`、識別子に対して該当ファイルの絶対パス、対象ID、未記録の差分のみを送信する。状態の記録が存在しない場合は、未完了の事項と検収済みの状態を委譲プロンプト内にまとめて記述する（レビュー指摘管理表が必要な場合は委譲元がmanaged-temp配下に作成し、絶対パスで渡す）。
- すでに委譲した要求について自ら本文を精読したり、追加調査したり、回答を作成したりする前に、詳細な文脈を保持している元の担当者を確認する。その担当者に対してユーザーの原文、未確定の論点、参照記録を引き渡し、技術的な調査と判断を委ねる。担当者が稼働中であれば追加指示を送り、終了していれば前述の継続・回復・置換の条件に従って処理する。委譲元は認可、全体調整、検収の責任を担い、判断に必要な根拠を確認する。独立した文脈でのレビュー責任や元担当者の終了確認を維持し、書込権限の移動は`references/mandatory-rules.md`「作業ツリーの保護」に従う。
- 独立したレビュー系統、異なる観点からの評価、先行履歴を前提としない検証は、新しい識別子を用いて起動する。この新規起動は、前項で禁止している同一目的の調査の重複には当たらない。
- 同一の応答内で複数の継続指示を発行する場合は、送信前に各呼び出しの宛先識別子と本文の組み合わせが正しいことを確認する。
- ユーザーによる介入が生じた場合は、直ちに強制停止するのではなく、`agent-toolkit:/share/exec-review-responses.md`「指摘発生時の扱い」に従って採否を確定した差分のみを追加指示として委譲先へ伝える。作業を停止するのは、介入によって作業対象や前提が無効になり、続行すると誤った成果物が生成される恐れがあり、かつ稼働中の作業が完了するまで対応を先送りできない場合に限定する。
- 委譲先を停止した場合は、再起動する前に停止時点の成果物を検収する。完了条件を満たしていれば委譲元が成果を引き取る。未完了の場合は、委譲元が残りを引き取るか、完了済みの工程、試行済みのアプローチ、確認された失敗内容を渡して続きから再起動するかを`references/routing.md`「要否判定」で選ぶ（書込所有権の引き取りは`references/mandatory-rules.md`「作業ツリーの保護」に従う）。

## 受領と検収

exploreおよびshellの受領と検収は「explore・shellの起動」の最終項目に従い、本節ではそれ以外の委譲を対象とする。

完了報告は次の順序で処理する。各工程の詳細は`references/receiving.md`の同名節に従う。

1. 委譲先の終了を確認した上で成果本文を受領し、通常完了の報告形式を選択する（「受領手段と報告様式の選択」）。
2. 受領した本文を役割固有形式と共通返却契約の組合せで確認し、サーバーの構造回復済みの診断を区別して、必要な形式修正を求める（「返却形式の適合確認」）。
3. 実行識別子、必須項目、対象、権限、完了条件との整合性を確認し、自身の委譲元へ結果を返す（「委譲元への返却」）。
4. 対象ファイル、退避された成果物、指摘の採否、`気付いた改善点:`の記述を適切に処理する（「受領後の扱い」）。

サブエージェントの出力（レビュー指摘や調査結果）は、指定されたレビュー指摘管理表に記録してから修正作業に着手する。指摘を追加するエージェントは`track`を指定して記録し、`指摘内容`に実際値、期待値、違反している規範や契約の出典、対象への適用根拠を明記する。レビュー対象者は、該当行を一意に特定するキーを用いて応答欄を更新する。
応答欄の記入時は、事前に`agent-toolkit:/share/exec-review-responses.md`「レビュー指摘管理表への応答」を読み、指定された行頭ラベルと構成に従って記述する（ラベル不足時は`atk review-table respond`の実行が失敗する）。指摘を採用しない（no）行の`根拠の所在:`には、メインエージェントの判断とユーザーの判断のどちらに基づくかも明記する。

## 停滞の検知と巻き取り

観測の優先順位は`references/waiting-and-monitoring.md`「待機区間の構成」に従い、Codex環境で元の担当者が稼働している間の処理保留は`references/codex-runtime.md`「後続操作の共通先行条件」に従う。

- Codex以外の環境で、同一種別・同一用途のエージェント起動を繰り返しても収束しない場合は、`references/runtime-routing.md`「継続と新規起動」に従って担当者、作業単位、モデル、実行手段を見直す。
- 委譲先の役割を自身で巻き取る場合は、その役割の`<役割名>.subagent.md`に定められた義務（判断の責任主体、加工せずに伝達すべき対象、収束条件）を自身に適用して遂行する。

## 再委譲する場合に守る事項

上位のエージェントから委譲を受けたエージェントがさらに別のエージェントへ委譲する場合は、本スキルが委譲元に対して定めている規定を自身に適用する。委譲先全般に関する共通規定は`agent-toolkit/share/rules-subagent.md`に従う。

- 上位から受け取った取り決め（作業場所、書込を行う主体、出力言語、プロセスの所有権、不可逆操作の権限と許可範囲）を同一の意味で引き継がせ、子孫エージェントの完了本文を自身で受け取って自身の完了報告に含める。

## 工程で参照する運用資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 事象単位の調査を委譲する前 | `agent-toolkit:delegation/references/investigation-delegation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/investigation-delegation.md` | |
| 受入シナリオの文書試行・常駐サービスの公開前観測の前 | `agent-toolkit:delegation/references/acceptance-scenarios.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/acceptance-scenarios.md` | |
| エージェント向け文書の責務と配置を決める前 | `agent-toolkit:delegation/references/agent-documents-basics.md#「運用条件」「置き場所と配送」「読込表の宣言」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/agent-documents-basics.md#「運用条件」「置き場所と配送」「読込表の宣言」` | |
| ユーザーの入力素材を整理して取り込む時 | `agent-toolkit:delegation/references/writing.md`の「ユーザー入力素材の取扱い」 | 判断 | `section:agent-toolkit:*/skills/delegation/references/writing.md#「ユーザー入力素材の取扱い」` | |
| textlintなど文章チェックの診断へ対応する時 | `agent-toolkit:delegation/references/textlint-violations.md#「文の書き直しの委譲と検収」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/textlint-violations.md#「文の書き直しの委譲と検収」` | |
| 文章の書き直しを委ねる時 | `agent-toolkit:delegation/references/textlint-violations.md#「文の書き直しの委譲と検収」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/textlint-violations.md#「文の書き直しの委譲と検収」` | |
| エージェント向け文書の置き場所を選ぶ時 | `agent-toolkit:delegation/references/delivery-scope.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/delivery-scope.md` | |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂 | `agent-toolkit:delegation/references/sub-agents.md#「%60<役割名>.parent.md%60と%60<役割名>.subagent.md%60の構成」「agents_serverのfrontmatter」「起動対象と入力の案内」「返却形式の設計」「コンテキスト境界」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/sub-agents.md#「%60<役割名>.parent.md%60と%60<役割名>.subagent.md%60の構成」「agents_serverのfrontmatter」「起動対象と入力の案内」「返却形式の設計」「コンテキスト境界」` | |

## 参照資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/exec-review-responses.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` |
