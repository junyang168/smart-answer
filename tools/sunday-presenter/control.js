const labels = { worship: '主日崇拜', sermon: '講道' };
const root = document.querySelector('#decks');
const error = document.querySelector('#error');
for (const [deck, label] of Object.entries(labels)) {
  const section = document.createElement('section');
  section.className = 'deck';
  section.id = deck;
  // Only fixed application strings enter this template. Remote titles use textContent.
  section.innerHTML = `<h2>${label}<button class="take" data-deck="${deck}">切換到${label}</button></h2>
    <form><input type="url" required placeholder="貼上 PowerPoint / OneDrive 連結" aria-label="${label}連結"><button>開啟</button></form>
    <button class="office">Microsoft 登入 / 選檔</button><div class="pages"></div>`;
  section.querySelector('.office').onclick = () => act('office', { deck });
  section.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const input = section.querySelector('input');
    if (await act('open', { deck, url: input.value.trim() })) input.value = '';
  };
  section.querySelector('.take').onclick = () => act('take', { deck });
  root.append(section);
}
async function act(action, args) {
  error.textContent = '';
  try {
    const reply = await window.presenter.act(action, args);
    if (reply.error) { error.textContent = reply.error; return false; }
    render(reply.state);
    return true;
  } catch { error.textContent = '操作未完成，請重試。'; return false; }
}
function render(state) {
  const website = state.website;
  const folder = website?.folder;
  document.querySelector('#sunday-date').textContent = folder ? `本主日：${folder.date} · ${folder.folderName}` : '本主日簡報';
  document.querySelector('#sunday-status').textContent = website?.status === 'loading' ? '正在向教會網站取得連結…' :
    website?.error || (folder ? (folder.folderExists ? 'OneDrive 資料夾已建立；雲端檔案以 OneDrive 同步狀態為準。' :
      '本週資料夾尚未建立；網站生成或上傳 PPT 後會建立，請稍後更新。') : '請更新連結。');
  for (const button of document.querySelectorAll('[data-folder]')) button.disabled = !folder;
  document.querySelector('[data-action="refresh-folder"]').disabled = website?.status === 'loading';
  const active = state.pages.find(page => page.id === state.active);
  document.querySelector('#live').textContent = active ? `輸出：${labels[active.deck]}` : '輸出：黑屏（全部靜音）';
  for (const deck of Object.keys(labels)) {
    const section = document.getElementById(deck);
    section.classList.toggle('live', active?.deck === deck);
    const chosen = state.pages.find(page => page.id === state.selected[deck]);
    section.querySelector('.take').disabled = !chosen?.ready;
    document.querySelector(`[data-take="${deck}"]`).disabled = !chosen?.ready;
    const pages = section.querySelector('.pages');
    const deckPages = state.pages.filter(page => page.deck === deck);
    for (const row of pages.children) {
      if (!deckPages.some(page => String(page.id) === row.dataset.page)) row.remove();
    }
    for (const page of deckPages) {
      // Keep buttons/focus stable when Office emits load/title events while an
      // operator is clicking. Only create/remove rows as pages open/close.
      let row = pages.querySelector(`[data-page="${page.id}"]`);
      if (!row) {
        row = document.createElement('div'); row.className = 'page'; row.dataset.page = String(page.id);
        const title = document.createElement('div'); title.className = 'title'; row.append(title);
        for (const [label, action] of [['準備 / 檢查', 'prepare'], ['設為觀眾畫面', 'arm'], ['關閉', 'close']]) {
          const button = document.createElement('button'); button.textContent = label; button.dataset.action = action;
          button.onclick = () => act(action, { id: page.id }); row.append(button);
        }
        pages.append(row);
      }
      row.querySelector('.title').textContent = `${page.id === state.active ? '● 播出中 · ' : ''}${page.title} · ${page.error || (page.ready ? '可切換' : '待檢查')}`;
      row.querySelector('[data-action="arm"]').disabled = state.preview !== page.id || !page.loaded || !!page.error;
    }
  }
}
for (const button of document.querySelectorAll('[data-action]')) button.onclick = () => act(button.dataset.action);
for (const button of document.querySelectorAll('[data-key]')) button.onclick = () => act('key', { key: button.dataset.key });
for (const button of document.querySelectorAll('[data-take]')) button.onclick = () => act('take', { deck: button.dataset.take });
for (const button of document.querySelectorAll('[data-folder]')) button.onclick = () => act('sunday-folder', { deck: button.dataset.folder });
window.presenter.onState(render);
act('state');
