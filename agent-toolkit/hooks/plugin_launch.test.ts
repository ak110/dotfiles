import { expect, test } from "claude-code/testing";
import { pluginLaunchCommand } from "./plugin_launch.ts";

test("共有環境のlaunch.py起動argvを組み立てる", () => {
  expect(pluginLaunchCommand("/plugin", "agent_toolkit/atk.py", ["wait-schedule"])).toEqual(["uv", "run", "--no-config", "--no-project", "--python", "3.15", "python", "/plugin/scripts/launch.py", "/plugin/agent_toolkit/atk.py", "wait-schedule"]);
});
