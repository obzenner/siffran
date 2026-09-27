declare module "@earendil-works/pi-coding-agent" {
  export interface HostSettings {
    [key: string]: unknown;
  }
  export class SettingsManager {
    static create(cwd: string, agentDir?: string): SettingsManager;
    getGlobalSettings(): HostSettings;
    getProjectSettings(): HostSettings;
  }
  export function getAgentDir(): string;
}
