import { expect, mock, test } from "claude-code/testing";
import { languageCheckNotice } from "./response_language.ts";

const ENGLISH = "The investigation is complete and the results have been verified.";

for (const delegated of [false, true]) {
  for (const previousText of [ENGLISH, ""]) {
    test(`通常操作は直前本文の言語を判定しない（委譲先=${delegated}、本文あり=${Boolean(previousText)}）`, async ($, on) => {
      mock.env(on, delegated ? { AGENT_TOOLKIT_DELEGATED_SESSION: "1" } : {});
      let messageReads = 0;
      let checks = 0;
      let executed = 0;
      on("session.messages", () => {
        messageReads++;
        return { value: [{ role: "assistant", content: [{ type: "text", text: previousText }] }] };
      });
      on("process.run", () => {
        checks++;
        return { value: { exitCode: 0, stdout: JSON.stringify({ outcome: "warn", body: "英語主体の本文。", fix: "日本語で書き直す。" }), stderr: "", isStdoutTruncated: false, isStderrTruncated: false } };
      });
      on("tool.call", { tool: "Bash" }, () => { executed++; return { result: "実行済み" }; });
      on("tool.call", { tool: "Read" }, () => { executed++; return { result: "読込済み" }; });
      const first = await $.tool.call({ tool: "Bash", command: "ls" });
      expect(first.result).toBe("実行済み");
      expect(first.deny).toBeUndefined();
      expect(first.context ?? []).toEqual([]);
      const following = await $.tool.call({ tool: "Read", file_path: "/repo/a.txt" });
      expect(following.result).toBe("読込済み");
      expect(following.deny).toBeUndefined();
      expect(following.context ?? []).toEqual([]);
      expect(executed).toBe(2);
      expect(messageReads).toBe(0);
      expect(checks).toBe(0);
    });
  }
}


test("言語検査の失敗は元の操作を再発行せず、是正の結果を伝える", () => {
  const message = languageCheckNotice("検査スクリプト、終了コード 2");
  const noReplay = message.indexOf("元の操作は再発行するな");
  const cause = message.indexOf("原因を確かめる");
  const repair = message.indexOf("認可内の配布物・設定を修復");
  const report = message.indexOf("回復結果か");
  expect(message.includes("終了コード 2")).toBe(true);
  expect(noReplay >= 0 && noReplay < cause && cause < repair && repair < report).toBe(true);
});
