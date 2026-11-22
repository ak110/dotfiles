import json
import os


def main() -> int:
    if os.environ["OWNS"] == "false":
        print(os.environ["OWNER_REASON"])
        return 0
    needs = json.loads(os.environ["NEEDS"])
    if os.environ["OWNS"] != "true" or not needs or any(job["result"] != "success" for job in needs.values()):
        print("::error::必須checkの依存jobが未成功です。依存jobのログを確認し、修正後に再実行してください。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
