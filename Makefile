help:
	@cat Makefile

# 依存パッケージをアップグレードし全テスト実行
update:
	uv sync --upgrade --all-groups
	uv run pre-commit autoupdate
	$(MAKE) update-actions
	$(MAKE) test

# GitHub Actionsのアクションをハッシュピンで最新化（mise未導入時はスキップ）
update-actions:
	@command -v mise >/dev/null 2>&1 || { echo "mise未検出、スキップ"; exit 0; }; \
	GITHUB_TOKEN=$$(gh auth token) mise exec -- pinact run --update --min-age 1

# 開発環境セットアップ
setup:
	uv sync --all-groups
	uv tool install --editable .
	uv run pre-commit install
	@command -v pwsh >/dev/null 2>&1 || echo "警告: pwsh が未導入。PowerShell スクリプトの検証がスキップされる。Ubuntu/Debian なら 'make setup-pwsh' で一括導入可能"
	@command -v chezmoi >/dev/null 2>&1 || echo "警告: chezmoi が未導入。template 検証がスキップされる可能性あり"

# Ubuntu/Debian へ pwsh + PSScriptAnalyzer を一括インストールする。
# pre-commit の PSScriptAnalyzer / chezmoi template check (.ps1.tmpl) を
# ローカルでも実行可能にするための開発者向けターゲット。
setup-pwsh:
	sudo apt-get update
	sudo apt-get install -y wget apt-transport-https software-properties-common
	. /etc/os-release && \
	    wget -q "https://packages.microsoft.com/config/ubuntu/$$VERSION_ID/packages-microsoft-prod.deb" && \
	    sudo dpkg -i packages-microsoft-prod.deb && \
	    rm packages-microsoft-prod.deb
	sudo apt-get update
	sudo apt-get install -y powershell
	pwsh -NoProfile -Command "Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force -SkipPublisherCheck"

# フォーマット + 軽量lint（開発時の手動実行用。自動修正あり）
format:
	uv sync --frozen --all-groups
	SKIP=pyfltr uv run pre-commit run --all-files
	-uv run pyfltr --exit-zero-even-if-formatted --commands=fast .

# 全チェック実行（これを通過すればコミット可能）
test:
	uv sync --frozen --all-groups
	SKIP=pyfltr uv run pre-commit run --all-files
	uv run pyfltr --exit-zero-even-if-formatted .

.PHONY: help update update-actions setup setup-pwsh format test
