import argparse
import pathlib
import sys

# pyfltr: allow-comment 診断の収集は依存の導入に失敗した後にも実行されるため、その環境を使わずにimportする。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agent-toolkit"))
from agent_toolkit._common.diagnostic_redaction import redact_secrets  # noqa: E402  # pylint: disable=wrong-import-position


def main() -> None:
    parser = argparse.ArgumentParser(description="CIのartifactへ保存する診断を、認証値を伏せて複製する")
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("destination", type=pathlib.Path)
    args = parser.parse_args()
    safe = redact_secrets(args.source.read_text(encoding="utf-8", errors="replace"))
    args.destination.write_text(safe, encoding="utf-8")


if __name__ == "__main__":
    main()
