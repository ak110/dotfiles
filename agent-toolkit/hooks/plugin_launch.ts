export function pluginLaunchCommand(root: string, target: string, args: readonly string[] = []): string[] {
  return ["uv", "run", "--no-config", "--no-project", "--python", "3.15", "python", `${root}/scripts/launch.py`, `${root}/${target}`, ...args];
}
