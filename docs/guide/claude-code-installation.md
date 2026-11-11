# agent-toolkitの環境別導入

## Claudeの各環境への導入

使う環境と対応機能は[READMEの対応ホストと機能](../../agent-toolkit/README.md#対応ホストと機能)を参照。
ここではマーケットプレイス・ZIPからの追加とクラウドの詳細設定を扱う。

マーケットプレイスを追加する場合は、`Customize > Plugins > Add > Add marketplace`で`Add from a repository`を選び、`ak110/dotfiles`を入力して`agent-toolkit`を`Add`する。
同期範囲と組織による制限は[Claudeのプラグイン利用手順](https://support.claude.com/en/articles/13837440-use-plugins-in-claude)を参照。

### アップロード用ZIP

dotfilesのリポジトリで、公開済みcommitのプラグインだけをZIPへ入れる。

```bash
git archive --format=zip --output=agent-toolkit.zip HEAD:agent-toolkit
```

ZIPにはGitの追跡対象だけが入る。
展開後の容量200 MB・5,000ファイル以内などの受付条件は[公式のプラグインアップロード要件](https://platform.claude.com/docs/en/manage-claude/plugins-api#upload-requirements)を参照。

### Claude Codeクラウドの環境設定

Claude Code 2.1.280以上のクラウド環境で、環境の設定画面の2つの欄へ次を保存する。

`Environment variables`欄:

```dotenv
CLAUDE_CODE_PLUGIN_DIRS=/opt/agent-toolkit-src/agent-toolkit
AGENT_TOOLKIT_RUNTIME_DATA=/opt/agent-toolkit-data
```

`Setup script`欄:

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude-cloud.sh | bash
```

ネットワークは`Trusted`など、GitHubとPythonパッケージ配布先へ接続できる設定にする。
環境変数は`Environment variables`欄へ保存する（セットアップ内の`export`は後続セッションへ引き継がれない）。
保存後、新しいセッションでスキル一覧とMCPの接続を確認する。
セットアップが失敗した場合は、出力の末尾に失敗した段と確認事項が表示される。

取得先と依存領域を変える場合は、変数をパイプの右側の`bash`の直前へ置き（行頭へ置くと`curl`だけに渡る）、表示された2行を`Environment variables`欄へ保存する。
取得先は絶対パスにする。

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude-cloud.sh | AGENT_TOOLKIT_CLOUD_SRC=<取得先> AGENT_TOOLKIT_RUNTIME_DATA=<依存領域> bash
```

setupの制限時間とネットワーク設定は[クラウド環境の設定](https://code.claude.com/docs/en/cloud-environments)を参照。
