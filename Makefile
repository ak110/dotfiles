# サプライチェーン攻撃対策としてlockfileを常に尊重する。依存を更新する場合のみ
# `env -u UV_FROZEN` で一時的に無効化する（`UV_FROZEN=` の空文字代入はuvがエラー扱い）。
export UV_FROZEN := 1

help:
	@cat Makefile

# 依存パッケージをアップグレードし全テスト実行
update:
	env -u UV_FROZEN uv sync --upgrade --all-groups
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
	sudo apt-get install --yes wget apt-transport-https software-properties-common
	. /etc/os-release && \
	    wget --quiet "https://packages.microsoft.com/config/ubuntu/$$VERSION_ID/packages-microsoft-prod.deb" && \
	    sudo dpkg --install packages-microsoft-prod.deb && \
	    rm packages-microsoft-prod.deb
	sudo apt-get update
	sudo apt-get install --yes powershell
	pwsh -NoProfile -Command "Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force -SkipPublisherCheck"

# フォーマット + 軽量lint（開発時の手動実行用。自動修正あり）
format:
	uv sync --all-groups
	SKIP=pyfltr uv run pre-commit run --all-files
	-uv run pyfltr fast .

# 全チェック実行（これを通過すればコミット可能）
test:
	uv sync --all-groups
	SKIP=pyfltr uv run pre-commit run --all-files
	uv run pyfltr run .

.PHONY: help update update-actions setup setup-pwsh format test
