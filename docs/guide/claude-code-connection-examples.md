# 接続の設定例

接続種別の準備は[接続の使い分け](claude-code-connections.md)を参照。

## 接続の使い分け

### 設定例

各例のコードブロックは、そのまま貼り付けて実行すると両エンジンの接続設定8キーを全て保存する。
envファイルのパスとprovider IDは、前節で用意した名前と異なる場合だけ置き換える。
使わない種別は空文字列を保存して解除する。

サブスクリプションだけを使う環境では、次のとおり設定する。
両エンジンとも統括担当と委譲先はサブスクリプションだけを使い、親の環境変数にあるAPIキーなどは使わない。

```bash
atk config set claude_credits_env_file ''
atk config set claude_payg_env_file ''
atk config set claude_orchestrator_connections subscription
atk config set claude_worker_connections subscription
atk config set codex_credits_provider ''
atk config set codex_payg_provider ''
atk config set codex_orchestrator_connections subscription
atk config set codex_worker_connections subscription
```

サブスクリプションとAPIクレジットを使う環境では、次のとおり設定する。
委譲先はAPIクレジット、サブスクリプションの順に使う。
統括担当はサブスクリプション、APIクレジットの順に使う（Claudeの`atk wi process-loop`はAPIクレジットを使えないためサブスクリプションだけを使う）。

```bash
atk config set claude_credits_env_file '~/.config/claude-credits.env'
atk config set claude_payg_env_file ''
atk config set claude_orchestrator_connections subscription,credits
atk config set claude_worker_connections credits,subscription
atk config set codex_credits_provider my-credits
atk config set codex_payg_provider ''
atk config set codex_orchestrator_connections subscription,credits
atk config set codex_worker_connections credits,subscription
```

サブスクリプションと従量課金APIを使う環境では、次のとおり設定する。
統括担当と委譲先はサブスクリプションを先に使い、利用上限に達した委譲先と、サブスクリプションを使えない統括担当だけが従量課金APIを使う。

```bash
atk config set claude_credits_env_file ''
atk config set claude_payg_env_file '~/.config/claude-payg.env'
atk config set claude_orchestrator_connections subscription,payg
atk config set claude_worker_connections subscription,payg
atk config set codex_credits_provider ''
atk config set codex_payg_provider my-payg
atk config set codex_orchestrator_connections subscription,payg
atk config set codex_worker_connections subscription,payg
```

3種類すべてを使う環境では、次のとおり設定する。
委譲先はAPIクレジット、サブスクリプション、従量課金APIの順に使う。
統括担当はサブスクリプション、APIクレジット、従量課金APIの順に使う（Claudeの`atk wi process-loop`はAPIクレジットを使わない）。

```bash
atk config set claude_credits_env_file '~/.config/claude-credits.env'
atk config set claude_payg_env_file '~/.config/claude-payg.env'
atk config set claude_orchestrator_connections subscription,credits,payg
atk config set claude_worker_connections credits,subscription,payg
atk config set codex_credits_provider my-credits
atk config set codex_payg_provider my-payg
atk config set codex_orchestrator_connections subscription,credits,payg
atk config set codex_worker_connections credits,subscription,payg
```

APIだけを使う環境では、次のとおり設定する。
両エンジンとも統括担当と委譲先は従量課金APIだけを使い、使用中の会話も次の継続から従量課金APIへ移る。

```bash
atk config set claude_credits_env_file ''
atk config set claude_payg_env_file '~/.config/claude-payg.env'
atk config set claude_orchestrator_connections payg
atk config set claude_worker_connections payg
atk config set codex_credits_provider ''
atk config set codex_payg_provider my-payg
atk config set codex_orchestrator_connections payg
atk config set codex_worker_connections payg
```

設定した値は`atk config show`で確かめる。
