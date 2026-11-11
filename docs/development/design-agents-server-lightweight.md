# 軽量起動と継続不能の設計記録

[設計記録の索引](design.md)から参照する、軽量起動と継続不能の記録である。

## 公開ツールの契約

### `explore`と`shell`

探索とコマンド分離を選ぶ量の目安は、tool_descriptions.pyのLIGHTWEIGHT_MODE_SELECTIONが1つの定義として持ち、server instructionsとstartのmodeの引数説明が同じ文を使う。
ツール検索の前に届くserver instructionsへ基準を置き、自ら読むか委譲するかの判断に先行させる。
常時規範を増やす案は読込費用が増えるため、遅延して取得するstartの説明だけへ基準を置く案はこの判断時点に届かないため採らない。
委譲元の起動から受領までの義務はdelegation/SKILL.md「explore・shellの起動」へまとめ、共有資料は節名で参照する。
起動のたびに共有資料の全文を読む案は、探索や出力分離が節約する量と同程度以上の文脈を先に消費するため採らない。
作業全体の分割を判定する時点は、1件の探索・コマンドの起動とは分けてrouting.mdへ到達させる。

`explore`は対象リポジトリの成果物、委譲入力およびユーザーデータを読み取り専用で調査し、結論と根拠を返す。
読取ツールが管理するキャッシュ・状態・保存出力は対象成果物と区別し、担当自身の成果ファイルは作成しない。
大きな検索結果は条件の絞込みか分割取得で判断に必要な範囲を最後まで読み、省略や未読から不在・件数・網羅性を結論しない。
渡された資料で確定できない事項は未確認事項として返す。

全backendのexploreはGitの読取照会、本文・属性検索、標準の読取（Read、`cat`、`sed -n`の行範囲、JSONは`jq`）と`pyfltr grep`などの読取MCPを利用する。
対象成果物・委譲入力・ユーザーデータの変更禁止は共通プロンプトが所有する。
実行環境のキャッシュ・状態・保存出力はこの禁止対象と区別する。
コマンドの閉じた許可集合は置かず、調査内容と候補engineの選択を分離する。

Claudeは軽量起動のスキル除外と通常の権限モードを保ち、組込presetと配布定義のpyfltr MCPを使う。
Codexは開始・再開へ`project_doc_max_bytes=0`を渡し、`features.plugins=False`でスキル非配送を保つ。
pyfltr MCPは同じpluginの`.mcp.json`から直接のMCPサーバー設定へ渡し、通常の親環境を配送する。
開始・再開・後続turnのコマンド実行には通常起動と同じ実行環境を使う。
Antigravityも共通の起動候補・プロンプトとCLIの処理を使う。

保存資料は同じ版や観測時点を比較する目的で使い、取得可能な対象は担当自身がCLI・MCPで調査する。
読者別探索は保存資料とGitの対象版指定の双方を受理し、同じ版の本文・差分・未解決事項を比較する契約を保つ。
`launch_prompts.py`が全backendへ探索指示と`EXPLORE_COMMON_CAPABILITIES`・`EXPLORE_BACKEND_CAPABILITIES`を組み立て、公開説明も同じ能力定義を使う。
完成条件から全文読取と所在探索を分け、全文が必要な長文・保存結果はハーネスの読取か`sed -n`で行範囲に分けて末尾まで担当内で取得する。
取得量は返却全体のUTF-8予算へ合わせ、Codexでは内側コマンドと外側セルの両方へ収める。
権限・到達不能・入力不足は担当内で補える省略と区別して返す。
全体の対象集合は委譲元が保持し、公開操作・共通定義・呼出元から独立処理群を欠落なく割り当てる。
文書群の単一応答上限と全体読取量は別に評価する。
各群は割当範囲を完了して返し、探索の意味上の完了は委譲元が検収する。
読了台帳・常設監視と本文の意味からの自動追送は追加しない。
探索結果は返却本文へ保持し、server自身のセッション記録保存と区別する。
各起動区分へ届く規範は`agent-toolkit:delegation/references/delivery-scope.md`の配送範囲表が示す。
1件の探索を選ぶ条件はserver instructionsの採算の目安、作業全体の分割は`agent-toolkit/skills/delegation/references/routing.md`、起動から受領は同スキルのSKILL.mdを知識境界とする。
検索時に読む`agent-basics:search`は切り出し条件への参照を持つ。
確定コマンドの実行はshell、自由な判断を伴う書込はdelegateへ渡し、対象と操作を委譲元が定める。

`start`の`shell`は`explore`と軽量な文書読込条件を共有し、コマンド実行専用の指示を渡す。
共有するのはchores_fast_modelの候補列、Codex backendの`project_doc_max_bytes=0`、Claude backendのユーザー設定に限った設定読込元とスキルの省略であり、exploreの書込制限は共有しない。
Claudeの軽量起動explore・write・shellは全て`disallowed_tools=["Skill"]`を指定し、新規とresumeでSkillを明示除外する。
通常起動にはこの除外を設けない。
Skillが無い主体は警告に記載されたSKILL.mdをReadで全文読む。
`explore`は`model_type`を省略すると`chores_fast`を使う。
段位は作業の部分ごとに選ぶ。
判定基準を言語化して渡せる多数の対象の走査は軽量段位の`explore`へ分ける。
観点・評価軸・判定基準の選定と新しい候補の案出し、走査が返した候補の判定と結論の統合は委譲元の`execute`担当自身が行い、そのための調査を`execute`の`explore`へ渡さない。
走査と案出しが混在する依頼も全体を`execute`にせず、読取・走査を分けて軽量段位へ渡す。
軽量側の候補では判断材料が不足する中規模の読取・要約・走査は`chores`を指定する。
`execute`を指定してよいのは、レーン担当の起動と、役割文書が`execute`の工程を定める担当に限る。
それ以外にエージェントが自分の判断で起動する委譲は、`review`・`review_fast`・`chores`・`chores_fast`・`write`のいずれかにする。
2026年11月1日のユーザー指示による規定で、計画起草や調査の目的でexecuteの下位担当を起動し、上位段位の費用が膨らんだ観測が契機である。
2026年10月24日のユーザー指示により、それまでの「判定を含む調査は`explore`でも`execute`を明示する」規定を改めた。
同日のprocess-wiのレーンで、内部の担当が現状調査と要約を主とする`explore`5件を全て`execute`で起動し、上位段位で読取を行った観測が契機である。
判定を委譲元に残せば、判定の品質を保ったまま読取の費用を軽量段位へ移せる。
実行レビューなどの工程別割当は維持し、調査の段位を起動方法だけで選ぶ案は採らない。
走査を軽量段位へ分けるのは、案出しを含む調査の全体を上位段位で行うと、基準を渡せば軽量段位でも判定できる多数の対象の読取まで上位段位の料金で行い、範囲の広い見直しの費用が大きくなるためである。
以前の規定は案出しと機械的な事実確認を区別して段位を選ぶ目的で、混在する依頼の全体を`execute`にしていた。
案出しと判定を上位段位に残せばこの目的は保たれる。
類似見直しでも走査を軽量段位へ委ね、非該当と返った対象を上位段位で全件再検証しない。
現に動いている成果物には致命的な問題が少ないとみなし、母集団は縮めずに上位段位の読取を該当・判定保留の対象へ集める。
これは`agent-basics:bugfix`の`agent-basics:bugfix/references/root-cause-severity-impact.md`「QとC・Dの区別」が許すQを満たす手段どうしの費用比較に当たる。
この起動条件は2026-09-01にCodex 0.151.0とClaude Agent SDK 0.2.148で実際に動かして確かめた。
Codexの`thread/start`は`config={"project_doc_max_bytes": 0}`を受理し、作業ディレクトリ側の`AGENTS.md`だけを`instructionSources`から外す。
`CODEX_HOME`側のグローバル指示は残る。
Claude Agent SDKの`ClaudeAgentOptions`は`setting_sources`、`skills`、`tools`および`env`を受理し、空の設定読込元とスキル、`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`および組込tool presetを併用できる。
その後、軽量起動の設定読込元は`0d7ffbbe6`（2026-09-23）でユーザー設定（`setting_sources=["user"]`）へ変えたため、この確認の空の設定読込元は現行の起動条件と異なる。
この起動方式はClaude Code組込Exploreとの完全一致を要件にせず、同一認証・設定ディレクトリを維持した軽量化として扱う。
再検証ではCodexの`thread/start`応答の`instructionSources`と、SDKの`ClaudeAgentOptions`の公開フィールドを同じ版条件で確認する。
`CODEX_HOME`側のグローバル指示だけを読み込ませない設定は無い。
2026-09-03にcodex-cli 0.153.0の`codex app-server generate-json-schema`が出力する`ThreadStartParams`と、Codexの設定リファレンスが列挙する全設定キーを確認した。
`project_doc_max_bytes`は`AGENTS.md`から読む上限バイト数、`model_instructions_file`は組込指示の置換であり、いずれもグローバル指示だけを外す用途を持たない。
再検証は同じ2つの一覧から`instruction`、`doc`、`agents`を含むキーを抽出して確認する。

### 継続不能

継続不能の詳細な判定条件は`continuation.md`「継続と新規起動」が定める。
`atk agents wait`は保持期限を過ぎて結果を回収済みのsessionへ`status`が`expired`の応答を返すが、同じsessionの会話再開用の最小状態は保持され、
`send_message`は保持された会話再開用の最小状態から暗黙再開するため、保持期限の超過だけを継続不能の根拠にしない。
呼び出し側が`atk agents wait`の`expired`の応答を継続不能と扱う運用は、再開できるthreadを破棄して規範・計画・対象コードの再読込を発生させるため採用しない。
保持期限内に継続させる別の運用を規範へ追加する案も、暗黙再開が同じ結果を与えるため採用しない。
