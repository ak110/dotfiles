---
paths:
  - "plugins/**/scripts/*.py"
  - "plugins/**/hooks/**"
  - "scripts/claude_hook_*.py"
---

# フック / プラグインの開発方針

## systemMessage / stderrメッセージの記述言語

hookやプラグインが出力するメッセージ（`systemMessage`およびblock時のstderr）は英語で記述する。
これらのメッセージはユーザーの作業コンテキスト（日本語の思考の流れ）に注入されるため、
日本語で書くとシステム出力とユーザーの思考が視覚的に区別しにくくなる。
英語にすることでシステムメッセージであることが一目で判別できる。
