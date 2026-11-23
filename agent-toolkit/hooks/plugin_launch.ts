const generationRoot: string = "";

export function pluginLaunchRoot(root: string): string {
  return generationRoot || root;
}

export function pluginLaunchCommand(root: string, target: string, args: readonly string[] = []): string[] {
  const launchRoot = pluginLaunchRoot(root);
  return ["uv", "run", "--no-cache", "--no-config", "--no-project", "--python", "3.15", "python", `${launchRoot}/scripts/launch.py`, `${launchRoot}/${target}`, ...args];
}
