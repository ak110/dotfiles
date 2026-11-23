import { expect, mock, test } from "claude-code/testing";

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
