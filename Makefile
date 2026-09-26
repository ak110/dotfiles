# サプライチェーン攻撃対策としてlockfileを常に尊重する。依存を更新する場合のみ
# `env --unset=UV_FROZEN` で一時的に無効化する（`UV_FROZEN=` の空文字代入はuvがエラー扱い）。
export UV_FROZEN := 1

help:
	@cat Makefile

# 依存パッケージをアップグレードし全テスト実行
update:
	env --unset=UV_FROZEN uv sync --upgrade --all-groups --all-extras
	env --unset=UV_FROZEN uv lock --upgrade --project agent-toolkit
	mise exec -- prek autoupdate
	$(MAKE) update-mise-locks
	$(MAKE) update-actions
	$(MAKE) test

# リポジトリ用と配布用のmise lockfileを更新
# rustとdotnetは版を明示指定しており、明示指定した版は`mise lock --bump`が自分自身へ解決して
# 変更しないため、設定ファイルの版を書き換える`mise upgrade --bump`を併せて実行する。
# 配布用設定はGitLabバックエンドの`glab`を含み、トークンのfallbackを設定ファイルで無効化する。
update-mise-locks:
	mise upgrade --bump rust
	mise lock --bump --platform linux-x64,windows-x64
	MISE_CONFIG_DIR="$(CURDIR)/.chezmoi-source/dot_config/mise" mise upgrade --bump dotnet
	MISE_CONFIG_DIR="$(CURDIR)/.chezmoi-source/dot_config/mise" mise lock --global --bump --platform linux-x64,windows-x64
	# miseが生成する`.mise/locks`をchezmoiの`dot_mise/locks`へ移し、配布先の参照先へ届くようにする。
	rm -rf .chezmoi-source/dot_config/mise/dot_mise/locks
	mv .chezmoi-source/dot_config/mise/.mise/locks .chezmoi-source/dot_config/mise/dot_mise/locks
	rmdir .chezmoi-source/dot_config/mise/.mise

# GitHub Actionsのアクションをハッシュピンで最新化（mise未導入時はスキップ）
update-actions:
	@command -v mise >/dev/null 2>&1 || { echo "mise未検出、スキップ"; exit 0; }; \
	GITHUB_TOKEN=$$(gh auth token) mise exec -- pinact run --update --min-age=1

# 開発環境のセットアップ
setup:
	mise run bootstrap
	@command -v pwsh >/dev/null 2>&1 || echo "警告: pwsh が未導入。PowerShell スクリプトの検証がスキップされる。Ubuntu/Debian なら 'make setup-pwsh' で一括導入可能"
	@command -v chezmoi >/dev/null 2>&1 || echo "警告: chezmoi が未導入。template 検証がスキップされる可能性あり"

# ChromiumとLinuxのシステム依存を初期環境へ導入
# ルートとagent-toolkitは別々のuv.lockを持ちplaywrightの版が異なり得るため、両方の環境の版に対応するChromiumを導入する
setup-browser:
	uv run playwright install --with-deps chromium
	uv run --project agent-toolkit playwright install chromium

# Ubuntu/Debian へ pwsh + PSScriptAnalyzer を一括インストールする。
# prek の PSScriptAnalyzer / chezmoi template check (.ps1.tmpl) を
# ローカルでも実行可能にするための開発者向けターゲット。
setup-pwsh:
	sudo apt-get update
	sudo apt-get install --yes wget
	. /etc/os-release && \
	    wget --quiet "https://packages.microsoft.com/config/$$ID/$$VERSION_ID/packages-microsoft-prod.deb" && \
	    sudo dpkg --install packages-microsoft-prod.deb && \
	    rm packages-microsoft-prod.deb
	sudo apt-get update
	sudo apt-get install --yes powershell
	pwsh -NoProfile -Command "Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force -SkipPublisherCheck"

# フォーマット + 軽量lint（開発時の手動実行用。自動修正あり）
format:
	uv run --frozen pyfltr fast

# 全チェック実行（これを通過すればコミット可能）
# `--no-fix`はlinterの自動修正段を抑止する。コミット可否を判定するゲートが判定対象の作業ツリーを
# 書き換えないようにし、担当範囲外のファイルへ差分が出ないようにする。
# 自動修正が必要な場合は`make format`を使う。
test:
	uv run --frozen pyfltr run --no-fix

# 実ブラウザーテストを日常実行
test-browser:
	uv run playwright install chromium
	uv run --project agent-toolkit playwright install chromium
	AGENT_TOOLKIT_SERVE_BROWSER_TESTS=1 \
		uv run pytest agent-toolkit/agent_toolkit/_atk/serve/browser_test.py \
		-o addopts='' -p no:cacheprovider

# agents_serverの実backendを使うライブ一体テスト
test-agents-live:
	AGENT_TOOLKIT_LIVE_AGENTS_TEST=1 \
		uv run pytest agent-toolkit/agent_toolkit/agents_server_live_test.py \
		-o addopts='' -p no:cacheprovider

.PHONY: help update update-mise-locks update-actions setup setup-browser setup-pwsh format test test-browser test-agents-live
