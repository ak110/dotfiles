# 起動候補と観測identityの設計記録

[設計記録の索引](design.md)から参照する、起動候補と観測identityの記録である。

## backend構成と起動

### 起動候補と観測identity

Codexの接続先は工程別モデル候補と別に扱う。
役割別の使用順（`codex_orchestrator_connections`・`codex_worker_connections`）と種別ごとのprovider IDが、主接続先と後続の順を持つ。
provider IDは`codex_credits_provider`・`codex_payg_provider`が持ち、`subscription`は`openai`とする。
種別とprovider IDの対応は`_atk/codex_connection.py`が設定から解決し、クレジットと従量課金へ同じIDを設定することは拒否する。
保持したproviderから種別を一意に判定するためである。
4キーのどれも設定しない場合はCodexの通常設定へ従い、冷再開の前に`config/read`から実効providerを解決する。
設定がある場合は`codex_providers.py`がcwdを含む`config/read`の定義と`account/read`の認証種別を評価し、
使用順の先頭の利用可能な候補を明示する。
`subscription`はChatGPTログインの場合だけ使い、APIキーのアカウントでは除いて`openai`をAPI種別へ設定する方法を診断へ出す。
未定義・認証不備のAPIは後続候補へ進める。
全候補が使えなければ指定外の通常接続へ戻さず失敗する。
現在アカウントの`ordinaryUsageAllowed`と終了した`usageLimitExceeded`だけがサブスクからの移行を発火する。
疎な通知、使用率、解除予定時刻は回復の代用にしない。
APIの認証・モデル不受理等は後続APIへ有限に進む。
サブスクの一般的な失敗はAPI課金への移行にしない。

接続先の入力は前記の4キーと対応する環境変数だけが持つ。
旧`codex_model_providers`の保存値は設定読込時に1回だけ新キーへ移し（`openai`は`subscription`、それ以外のIDは1つに限り`codex_payg_provider`）、旧キーを削除する（2026年10月29日、ユーザー回答）。
`openai`以外のIDが2つ以上ある場合は移行先を一意に決められないため、新キーの設定を求めて止める。
定義・認証をatkへ複製する案は秘密の保持先と同期費用を増やし、Codex設定だけへ順序も移す案は
atkの1キーで選ぶ操作を成立させないため採らない。
選択順と接続先定義をそれぞれの所有者へ分ける。

失敗turnの終了後に購読を解除し、同じthread IDへ`modelProvider`を指定してcold resumeする。
応答のthread IDとproviderが一致し、会話状態の互換性を確認するまで次のturnを送らない。
closingは既存の有限待機を使い、
上書き無視・接続終了・応答喪失から新turnを推定して重複起動しない。
RPCとturnの排他は`codex.py`が持つ。
共有configやauthの差し替えは並行sessionと直接起動へ波及するため採らない。
新sessionへ元の要求を再送する案も履歴を失うため採らない。

選択済みIDは`SessionState`・`SessionResumeState`と既存登録簿の内部再開情報へ渡す。
会話状態の生成元は`codex_state_provider`で保持し、選択先の`codex_model_provider`と分ける。
新規開始と互換性を確認した再開だけが生成元を確定するため、旧版が保存した不適合な移行先を生成元として扱わない。
検査済みの同providerへの通常再開はその確認を使う。
接続切替と旧記録では`thread/read.path`か既存の記録解決からrolloutを読み、`session_meta.id`とproviderを確認する。
`response_item`と`compacted.replacement_history`内のcompaction・`encrypted_content`を、値を解釈せず不透明な状態として検出する。
`compacted.compaction_response_id`も圧縮状態への不透明な参照として扱う。
不透明な状態の生成元が再開先と異なるか不明な場合と、記録の欠落・不正では、次turnへ進めず`threadStateIncompatible`で終了する。
生成元の印が無い旧移行記録は、選択先と初期`session_meta`のproviderが異なり不透明状態がある場合、現在の選択先への再開も拒否する。
登録簿の`completed`だけでは状態生成先を確定できない。
元の記録と進捗を保持し、引き継ぎ起動を案内する。
生成元は解放済み再開情報にも保持し、既存の7日掃引へ委ね、公開射影には加えない。
利用枠の回復と主接続先の指定は新規起動だけへ作用し、API会話の接続先を戻さない。
後続候補は次の代替試行で現行列から選ぶ。
登録簿の解放済み記録にもCodexの内部再開情報を残し、stopと保持期限の経過のどちらによる解放後も復元する。
公開の解放判定・結果回収と7日掃引は維持する。
RPC失敗の追加dataは切替判定にだけ使い、公開するerrorへ取り込まない。
providerや切替情報は公開応答・状態・通知・結果・継続プロンプトへ加えず、資格情報なしの診断を
既存の`agents-server.log`だけへ残す。
turn開始の拒否を汎用文言`Codex connection rejected the request`へ置き換えるときも、起動時・切替時と同じく、session_id・接続先の識別子・元のエラーのcodeと本文を診断へ1行残す。
拒否本文と`codexErrorInfo`には認証値が含まれうるため、`codex_protocol.py`は共通の秘匿処理（`_common/diagnostic_redaction.py`）を通してから診断へ残す。
共通の秘匿処理は、認証ヘッダー、APIキー・token・secret・passwordの値、URLの認証情報、秘密鍵を伏せる。
拒否本文には、これに加えて連続32文字以上の鍵状文字列も伏せる。
鍵状文字列の規則を共通の秘匿処理へ広げないのは、原因調査に使うsession識別子やcommit OIDも伏せてしまうためである。
共通ログの保存handler、終了結果のエラー、前任の会話抽出の失敗診断も、共通の秘匿処理を通してから保存する。
同じ秘匿処理をturn開始時の一般例外にも適用し、改行はエスケープして診断を1行に保つ。
codeと秘匿対象以外の拒否理由を残すことで、資格情報を保存せず原因を追跡できる。
公開するerrorには、原因が同じsession_idの診断ログにあることを示す案内だけを加える。
認証token・APIキーは登録簿へ保存しない。

起動要求・解決候補と、物理記録から観測した実行identityは別フィールドで返す。
開始時の観測値は未確定とし、終了時に観測値を返す。
既存の`engine`、`model`、`effort`を互換上の起動値として残す場合も意味を変えない。

Codex backendの観測identityは`turn_context`を観測元とし、表示名はモデルカタログを優先し、無い完全IDだけを決定論的に変換する。
取得不能値を推測しない。

選定結果の担当モデル、起動候補および終了後の観測identityを別の値として扱う。
観測値を候補や設定から補わない。

Claudeの接続先は共通の役割判定で選ぶ。
`claude_credits_env_file`・`claude_payg_env_file`はenvファイルの所在、`claude_orchestrator_connections`・`claude_worker_connections`は役割別の使用順を持つ。
4キーのどれも設定しない新規会話は`inherit`を登録し、親の環境変数とClaude Codeの通常の認証解決を使う。
SDKの環境継承と親settingsの認証設定を保ち、認証検出で起動を拒否せず、接続を切り替えない。
接続を管理しない間は再開時も`inherit`を保持する。
接続情報もAPIパスも持たない旧形式の登録は同じ通常の認証解決で読む。
接続を管理し始めた後は、これらの会話も使用順の種別ではないため次の継続で使用順の先頭の利用可能な候補へ移す。
以下の役割別接続管理はいずれかのキーを設定した環境に適用する。
委譲先は`claude_worker_connections`（既定`credits,subscription`）、`orchestrate`用途はオーケストレーターとして`claude_orchestrator_connections`（既定`subscription,credits`）の順に、認証のある種別を使う。
使える候補が無い場合は起動を止める。
空か無効なAPI環境ファイルは候補から除き、非空の無効パスを修正操作とともに返す。
通常の委譲先は起動前の利用不能や利用枠・API残高の不足で使用順の次の接続へ進み、同じsession ID・model・effortで継続する。
利用枠超過の解除待ちは`claude_usage_limit_wait`で判定する。
待機対象外の上限で全候補が尽きた場合と認証手段が無い場合は、候補ごとの理由と復旧操作を返して終了する。
新規sessionは毎回先頭から評価する。
利用上限によりサブスクリプションからAPIへ移した委譲先は、使用順でサブスクリプションが現在の種別より前にあれば、上限の解除予定時刻の後の最初の追送か自動再開で同じ会話をサブスクリプションへ戻す（理由は[design-agents-runtime.md](design-agents-runtime.md)）。
役割と選択済みの接続（`credits`・`payg`・`subscription`・`inherit`）、APIの絶対パスを登録簿の再開情報へ保持し、設定変更・解除、stop・保持期限、サーバー再起動後も同じ接続を使う。
保持した種別を使用順から外した場合だけ、次の継続で使用順の先頭の利用可能な候補へ移す。
旧形式のAPIパスだけを持つ登録と種別`api`の登録は、保存済みのenvファイルがクレジットのものと一致すれば`credits`、それ以外は`payg`の会話として読む。
値は切替と再開の起動ごとに読み、登録簿へ複製しない。
起動環境と合成settingsの`env`の両方へ渡すのは、通常のClaude Codeではsettingsの同名変数が起動環境より優先されるためである。
合成settingsは所有者だけが読める起動別ファイルで渡し、接続終了・起動失敗時に回収する。
環境変数値をatkへ保存する案と、起動環境だけへ渡す案は、それぞれ秘密の複製と設定の競合を生むため採らない。
ログはsession IDと利用枠の種類の1行に限り、公開応答・状態・継続指示へ切替情報を加えない。
親のAPI認証はSDKの継承環境と親settingsの両方で遮断し、選択したAPIファイルの値を優先する。
