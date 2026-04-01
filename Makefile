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

# ruff自動修正
fix:
	uv run ruff check --fix --unsafe-fixes

# フォーマットのみ（pyfltrはfast）
format:
	uv sync --frozen --all-groups
	SKIP=pyfltr uv run pre-commit run --all-files
	-uv run pyfltr --exit-zero-even-if-formatted --commands=fast .

# 全チェック実行
test:
	uv sync --frozen --all-groups
	SKIP=pyfltr uv run pre-commit run --all-files
	uv run pyfltr --exit-zero-even-if-formatted .

.PHONY: help update update-actions setup fix format test
