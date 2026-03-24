help:
	@cat Makefile

# 依存パッケージをアップグレードし全テスト実行
update:
	uv sync --upgrade --all-groups
	uv run pre-commit autoupdate
	$(MAKE) test

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
	-uv run pyfltr --exit-zero-even-if-formatted --commands=fast pytools/ tests/

# 全チェック実行
test:
	uv sync --frozen --all-groups
	SKIP=pyfltr uv run pre-commit run --all-files
	uv run pyfltr --exit-zero-even-if-formatted pytools/ tests/

.PHONY: help update setup fix format test
