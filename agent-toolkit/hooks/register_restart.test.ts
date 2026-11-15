import { expect, mock, test } from "claude-code/testing";

test("JSON再開印を送信前にresumedへ変更し生成元付きで一度だけ続行する", async ($, on) => {
  mock.env(on, { HOME: "/home/tester" });
  const clock = mock.clock(on);
  let record = JSON.stringify({ phase: "pending", waits: [{ id: "old-wait", command: "atk agents wait" }] });
  const prompts: string[] = [];
  on("session.start", (_$, e) => ({ cwd: e.cwd }));
  on("session.id", () => ({ value: "restart-main" }));
  on("command.register", (_$, e) => ({ value: { command: e.name } }));
  on("fs.read", (_$, e) => ({ value: e.path.endsWith("/restart-restart-main.txt") ? record : "consumed" }));
  on("fs.write", (_$, e) => { if (e.path.endsWith("/restart-restart-main.txt")) record = e.text; return { value: undefined }; });
  on("prompt.submit", (_$, e) => {
    expect(JSON.parse(record).phase).toBe("resumed");
    prompts.push(e.text);
    return { text: "" };
  });
  await $.session.start({ cwd: "/work", surface: null, isInteractive: true });
  await clock.advance(0);
  await $.session.start({ cwd: "/work", surface: null, isInteractive: true });
  await clock.advance(0);
  expect(prompts.length).toBe(1);
  expect(prompts[0]).toContain('<atk-auto source="runtime-restart" kind="notice">');
  expect(prompts[0]).toContain("old-wait");
  expect(prompts[0]).toContain("引数なしのatk agents waitを背景で発行し直し");
});
