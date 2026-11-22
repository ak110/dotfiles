export type BackgroundWait = { id: string; command: string; startedAt: number; used: boolean; replExpiresAt?: number };
export type RuntimeChange = { id: string; targets: string[]; phase: "pending" | "running" | "done"; results: { target: string; text?: string; context?: readonly string[]; ref?: number; error?: string }[]; notified: boolean };

declare module "claude-code" {
  interface PluginState {
    "agent-toolkit": { uiMessages: Record<string, string | { message: string; placedAt: number }>; uiCalls: Record<string, string>; uiConsumed: Record<string, number>; runtimeChange: RuntimeChange | null; backgroundWaits: Record<string, BackgroundWait>; backgroundSeen: Record<string, boolean>; compactRequested: boolean | "resume"; turnRunning: boolean };
  }
}
