import { existsSync } from 'node:fs';
import { basename } from 'node:path';

export function browserOptions(env = process.env, platform = process.platform) {
  const executablePath = env.SUNDAY_WHATSAPP_CHROME;
  if (!executablePath || !existsSync(executablePath)) {
    throw new Error('Configure SUNDAY_WHATSAPP_CHROME with the installed standalone chrome-headless-shell executable');
  }
  const shell = basename(executablePath) === 'chrome-headless-shell';
  // A background process launched from the user's Chrome.app can receive Dock
  // reopen events after the user's normal browser quits, without opening a UI.
  if (platform === 'darwin' && !shell) {
    throw new Error('On macOS the WhatsApp agent requires standalone chrome-headless-shell; do not use the desktop Chrome.app');
  }
  return { headless: shell ? 'shell' : true, executablePath };
}
