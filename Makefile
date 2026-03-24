help:
	@cat Makefile

# 開発環境セットアップ
setup:
	uv tool install pre-commit
	uv tool install --editable .
	uvx pre-commit install

# 全チェック実行
check:
	uvx pre-commit run --all-files

.PHONY: help setup check
