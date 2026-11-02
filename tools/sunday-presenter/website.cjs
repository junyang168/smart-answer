const DEFAULT_API = 'https://dallas-hlc.org/api/admin/sunday-services/presenter/upcoming';

function comingSunday(now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Chicago', year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(now).map(p => [p.type, p.value]));
  const day = new Date(`${parts.year}-${parts.month}-${parts.day}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() + (7 - day.getUTCDay()) % 7);
  return day.toISOString().slice(0, 10);
}

function validateFolder(data, now = new Date()) {
  if (!data || data.date !== comingSunday(now) || data.timezone !== 'America/Chicago') {
    throw Error('網站返回的主日日期不符，請更新連結。');
  }
  const [year, month, day] = data.date.split('-').map(Number);
  const name = `${month}-${day}-${year}`;
  let url;
  try { url = new URL(data.folderUrl); } catch { throw Error('網站返回的 OneDrive 連結無效。'); }
  if (data.folderName !== name || url.protocol !== 'https:' || url.host !== 'onedrive.live.com' ||
      url.username || url.password || url.pathname !== '/my' ||
      !url.searchParams.get('id')?.startsWith('/personal/') ||
      !url.searchParams.get('id')?.endsWith('/' + name)) {
    throw Error('網站返回的 OneDrive 連結無效。');
  }
  return { date: data.date, folderName: name, folderUrl: url.href,
    folderExists: data.folderExists === true, cloudStatus: 'not_verified' };
}

class Website {
  constructor({ apiURL = DEFAULT_API, fetcher = fetch, now = () => new Date(), changed = () => {} } = {}) {
    const url = new URL(apiURL);
    if (url.username || url.password || (url.protocol !== 'https:' &&
        !(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)))) {
      throw Error('網站 API 必須使用 HTTPS；本機測試可使用 localhost。');
    }
    this.apiURL = url.href; this.fetcher = fetcher; this.now = now; this.changed = changed;
    this.state = { status: 'idle', folder: null, error: '' };
  }
  async refresh() {
    if (this.pending) return this.pending;
    this.state = { status: 'loading', folder: null, error: '' }; this.changed();
    this.pending = (async () => {
      try {
        const response = await this.fetcher(this.apiURL, {
          cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(12000)
        });
        if (!response.ok) throw Error(`網站暫時無法提供本主日連結（${response.status}）。`);
        const folder = validateFolder(await response.json(), this.now());
        this.state = { status: 'ready', folder, error: '' };
        return folder;
      } catch (error) {
        this.state = { status: 'error', folder: null, error: error.message.startsWith('網站') ?
          error.message : '無法連線到教會網站，請檢查網路後重試。' };
        throw Error(this.state.error);
      } finally { this.pending = null; this.changed(); }
    })();
    return this.pending;
  }
}
module.exports = { Website, validateFolder, comingSunday, DEFAULT_API };
