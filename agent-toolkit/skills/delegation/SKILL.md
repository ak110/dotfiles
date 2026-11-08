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

本文で名指しした資料と読込表の資料は、条件が成立したら判断や操作の前に指定節と、その成立に必要な前提・例外を自分で読む。全体が判断に必要な場合は全文を読む。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 委譲プロンプトや返却の文章を作成・検収する前 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/quality-standards.md` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

次に行う委譲・受領・待機の操作と、自身の役割から条件を選び、判断前に必要な資料・節と前提・例外を読む。hookの通知を待たずに選び、同じ版の取得済み本文は再利用する。会話圧縮後は現在の要求・認可・実行状態を復元し、再開する工程で必要な範囲を選び直す。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| Codexで委譲、待機、状態確認または中断へ着手する前（agents_serverのexplore・shellの起動から受領までを除く） | `references/codex-runtime.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/codex-runtime.md` | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `references/base-contract.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract.md` | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `references/mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `references/sending.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/sending.md` | |
| 作業の分担・委譲単位を決める前（計画起草前、独立した複数単位や長い工程の着手前を含む）、起動契約に手順と必須入力がない委譲を開始する前（explore・shellを除く）、確定した委譲構成を変更する前 | `references/routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` | |
| 委譲先とモデルを選定する時（explore・shell、および役割文書を持ち`model_type`を指定しない起動を除く） | `references/runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` | |
| 委譲先とモデルを選定する時（explore・shell、および役割文書を持ち`model_type`を指定しない起動を除く） | `references/process-model-settings.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/process-model-settings.md` | |
| 元担当の継続・回復・置換・再起動または役割引継ぎを選ぶ前 | `references/continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/continuation.md` | |
| Claude Codeの`Agent`ツールで起動した同一担当を継続する前 | `references/continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/continuation.md` | |
| Claude Codeの`Agent`ツールで起動した同一担当を継続する前 | `references/agent-tool-continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/agent-tool-continuation.md` | |
| 可用性の障害を確認して代替起動を判断する前 | `references/fallback-launch.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/fallback-launch.md` | |
| 工程別設定の対象外でモデル・effortを選ぶ前 | `references/model-and-effort.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/model-and-effort.md` | |
| Claude Codeで`Agent`ツールの委譲先やバックグラウンドタスクを初回起動する前、定期再確認タスクを自身で設定する前、設定や回収の失敗通知に対処する時、Claude Code固有の手順を確定する時（explore・shellの起動から受領までの操作は除き、本書の同名節に従う） | `references/claude-code-runtime.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime.md` | |
| 完了通知の処理、ならびに待機や停滞の判定を行う時 | `references/waiting-and-monitoring.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md` | |
| 判定不能や拒否で待機を開始できない時、`atk agents wait`が終了コード4・8・10、または待機対象0件の非ゼロで終了した時、待機呼び出しがホストの上限により失敗した時、ホストからバックグラウンド待機の停止通知を受信した時、委譲先や自身のツール呼び出しが終了ステータスを返さずに終わった時、`agents_server`のMCPサーバーが再起動した時 | `references/wait-anomalies.md`の該当節 | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies.md` | |
| 稼働中の委譲先へ追加指示を送る前 | `references/base-contract.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract.md` | |
| 委譲済みの要求について自ら本文を読む、追加調査する、回答を作成する前 | 本書「継続と新規起動」、`references/routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` | |
| 同一worktreeに対して後続または並列の書き込み担当を起動する前（shellを除く。shellは本書「explore・shellの起動」に従う） | `references/mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` | |
| 実装担当・レビュー担当のworktreeと書込所有権を割り当てる前、およびそれらの担当を起動する前 | `references/implementation-worktree.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/implementation-worktree.md` | |
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

全起動経路の共通返却・エスカレーション契約は`share/rules/rules-subagent.md`「返却形式の受け渡し」が所有し、agents_serverが通常起動へ全文、軽量起動へ自己完結した短節を届ける。呼出元は役割固有の入力と結果だけを補い、形式未指定時は同節の正常結果の形式を用いる。taskの不正な続行不能に対する1回の自動回復と、その後の受領は`references/receiving.md`「返却形式の適合確認」に従う。

1. 担当と契約をそろえるため、定型の委譲内容は、呼び出し元の起動・受領契約と`<役割名>.subagent.md`で定義する。単一の呼び出し元だけで使う契約はその規範内へ統合し、複数の呼び出し元で共有する契約は`<役割名>.parent.md`として配置できる。
   評価の観点、判定基準、具体的な手順、返却形式は役割文書に記載する。委譲元は呼び出し元の契約に基づき、対象と実行時の値のみを渡す。自由記述のプロンプトによる起動は、役割文書が存在しない単発の作業に限定して用いる。
   役割固有の返却形式は役割文書の`## 出力`が定義を所有し、呼出側の既存の起動・受領契約へ必要な部分だけを生成配置する。
   委譲元はその配置先で入力と返却形式を確認し、起動と検収を進める。
   委譲元は起動の準備段階で`<役割名>.subagent.md`の本文を読み取らない。起動設定や必須入力の形式チェックはサーバーに任せる。この読取の省略は、ファイル自体の編集のための読取、委譲先自身による読取、完了後の形式確認とは区別する。

2. 適用する版をそろえるため、規範の変更を適用する委譲先には、作業ツリー原本の絶対パス、適用する節、対象の版を渡し、委譲先自身が判断前に読み込む。同じplugin rootに属する起動契約と役割定義をセットで使用し、すでに稼働しているCLI、フック、MCPが参照している版と作業ツリーの版を区別して扱う。
   役割文書を指定する委譲は、委譲元の会話履歴を引き継がない方法で起動する。
   Codex環境で`agents_server`が使えず組み込みの委譲で代用する場合は、`spawn_agent`に`fork_turns: "none"`を明示する。
   会話履歴を引き継ぐforkを選択できる条件は、`references/routing.md`「会話を引き継ぐ委譲」に従う。

3. 入力を確定するため、委譲プロンプトは、1行目の命令と必須入力の列挙という2つの要素で構成する。具体的な構成と列挙書式は`references/sending.md`「委譲プロンプトの構成」に従う。プロンプトに含めてよい要素は、`references/base-contract.md`の「入力の適格性と追送」で定める。exploreおよびshellの起動については次節に従う。

## explore・shellの起動

個別の調査やコマンド実行を自身で行うか、explore・shellへ委譲するかは、agents_serverのserver instructionsに記載されている採算の目安に基づいて判断する。`references/routing.md`の全文読込は、作業全体の分割を判定する際に行う。
両モードの起動から受領までに満たすべき必須の要件は本節で定める。括弧内の参照先は詳細情報の所在を示すものであり、実際の読込義務は前述の読込表の指定に従う。

1. 全体点検する際は、`references/routing.md`「コンテキスト消費が大きい調査の切り出し」に基づき、起動・初回読込・圧縮後再読・検収を含む総費用と分担の利点を比較する。公開操作、共通定義、呼び出し元、必要な情報量から担当範囲を定め、全体の管理と統合を委譲元が担う。

2. 起動前に対象、読取・書込の権限、完了条件、返却すべき観測値を確定する。委譲先が読み込む既存ファイルの絶対パスの実在、比較基準、実行する版を確認する。分量の多い調査では読取ツールの範囲指定を渡し、出力の切り詰めや上限到達を網羅性の根拠として扱わない（base-contract.md「起動前の確認」「分離実行の受領物」）。

3. 作業を分割する場合は`references/routing.md`「要否判定」を用い、モデルを選択する場合は`references/process-model-settings.md`「工程別モデル設定」に従う。明示的な指定がない場合は工程別の設定値を使用し、WIにモデル指定がある場合はそれを優先する。受領時には、指定値、起動候補、観測された実際の値を区別して扱う。

4. exploreは読取専用とする。shellの書込先は、生成物も含めて他の担当と重複しない範囲に限定し、同一担当の交代は先行する担当の終了後に実施する。同一worktreeでの書込の分担、作業ツリー全体へ影響する操作の直列化、別worktreeへ分離する条件は`references/mandatory-rules.md`「作業ツリーの保護」に従う。軽量起動では自動配送されないプロジェクト固有の指示や、コマンドの入力境界、終了状態、標準出力・標準エラー出力の保持を確実に引き渡す（base-contract.md「起動前の確認」、sending.md「権限と成果物の取り決め」）。

5. 入力は命令1文と必要な値（元の要求、対象、完了条件、返却形式、基準記録）にとどめ、すでに配布済みの制約、経緯、推測、既存記録の転記は含めない。自身で裏付けをとっていない値は未検証情報として明示する。exploreは読取専用として結果を返し、後続の入力には`atk agents wait`が保存した`agent_message_path`を直接指定する（base-contract.md「入力の適格性と追送」、sending.md「委譲プロンプトの構成」「未検証情報の取り扱い」）。

6. 各モードの入力項目や機能は、公開されている`start`の説明とスキーマで確認し、`cwd`に作業ディレクトリの絶対パスを渡す。exploreでは`prompt`を、shellでは`command`と`summary_policy`を使用する。スキルの手順を必要とする作業は`task`または`delegate`に委ねる（sending.md「起動と観測」）。

7. 返却された`session_id`を保持し、`atk agents wait`を用いて終了状態と結果本文を受け取る。待機の実行形式、終了コード、バックグラウンド化、通知後の受領手順は、読込表にある`references/waiting-and-monitoring.md`に従い、ホスト別の待機手順は後述の「軽量起動のホスト別待機」に従う。

8. 終了後に対象ファイルの差分、実行結果、診断情報を確認し、返却形式ならびに対象・権限・完了条件との整合性を検収する。件数、不在、網羅性は、省略のない取得結果に基づいて判定し、事実の主張は実物の根拠で裏付ける。受領前の段階で得られた情報のみで完了とみなさず、不足がある場合は同一担当へ差し戻す（receiving.md「返却形式の適合確認」「終了と報告内容の確認」、sending.md「権限と成果物の取り決め」）。

   結果本文の再保存は行わず、後続処理が参照する`agent_message_path`や待機実行の保存成果物は、参照が完了するまで保持する。保存期間と回収の手順は`agent-toolkit:managed-temp`に従う。

### 軽量起動のホスト別待機

Codexは`references/codex-runtime.md`「agents_serverの二層待機」、Claude Codeは`references/claude-code-runtime.md`「待機中の定期再確認と背景転換」に従う。軽量起動にも同じ識別子保持、回収、定期再確認の設定・再利用・削除条件を適用する。

## `<役割名>.parent.md`を持つ委譲の起動

呼び出し元の契約で起動手順が定められている委譲は、次の定型手順で起動する。本節の規定は、共有の`<役割名>.parent.md`を用いる場合と、単一の呼び出し元規範に統合された契約を用いる場合の両方に適用する。委譲元は起動前に該当する契約を読み、前提条件、入力値の決定規則、受領と検収の要件に従う。各契約には役割ごとに異なる引数値のみを記述する。

1. 入力をそろえるため、起動契約が定める絶対パスを`cwd`、役割名（例: `add-wi`）を`subagent_md_path`、名前付き入力を`extra_params`へ設定する。サーバーが自身のplugin rootの`share/<役割名>.subagent.md`を解決する。
   `mode`は指定しない。`model_type`は起動契約に値の決定規則がある場合にのみ指定する。省略された場合のmodeやmodel_typeは、サーバーが役割文書のfrontmatterから自動的に解決する。
   `引き継ぎ記録先`の扱いについては、`references/base-contract.md`「引き継ぎ記録先」の規定に従う。
   `extra_params`に設定する値には、`references/sending.md`「委譲プロンプトの構成」の「値へ書かない内容」と「未検証の値」、ならびに`references/base-contract.md`の`forwarded-user-input`標識と絶対パスの規定を適用する。
   必須入力の漏れや未宣言の入力名の拒否、`引き継ぎ記録先`の準備、応答言語の伝達、`<役割名>.subagent.md`の実在確認、`${CLAUDE_PLUGIN_ROOT}`の展開はサーバー側で処理する。サーバーから起動を拒否された場合は、エラー応答に示された項目と指示に従って再起動する。

2. 担当を開始するため、確定した引数で`agents_server`の`start`を1回呼び出す。

3. 起動後は返却された`session_id`を`atk agents wait`で観測する。Claude Code環境では、初回の`start`処理の中でagent-toolkitのmodが定期再確認タスクを設定し、その結果が`start`の返却後に通知される。

## 継続と新規起動

`references/continuation.md`「継続と新規起動」の番号順に進める。元担当の回復・置換・再起動・代替起動・引継ぎの前に、`references/codex-runtime.md`「後続操作の共通先行条件」を適用する。

## 受領と検収

explore・shellは「explore・shellの起動」の最終手順に従う。それ以外は`references/receiving.md`の番号順に、受領、形式と実体の検収、指摘の採否・改善点の処理を終えてから上位へ返却する。

## 停滞の検知と巻き取り

観測の優先順位は`references/waiting-and-monitoring.md`「待機区間の構成」に従い、Codex環境で元の担当者が稼働している間の処理保留は`references/codex-runtime.md`「後続操作の共通先行条件」に従う。

- Codex以外の環境で、同一種別・同一用途のエージェント起動を繰り返しても収束しない場合は、`references/continuation.md`「継続と新規起動」に従って担当者、作業単位、モデル、実行手段を見直す。
- 委譲先の役割を自身で巻き取る場合は、その役割の`<役割名>.subagent.md`に定められた義務（判断の責任主体、加工せずに伝達すべき対象、収束条件）を自身に適用して遂行する。

## 再委譲する場合に守る事項

上位のエージェントから委譲を受けたエージェントがさらに別のエージェントへ委譲する場合は、本スキルが委譲元に対して定めている規定を自身に適用する。

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
| レビュー指摘への応答、修正、修正後の完了を判断する前 | `agent-toolkit:/share/exec-review-responses.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` |
