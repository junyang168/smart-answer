const labels = { worship: '主日崇拜', sermon: '講道' };
const root = document.querySelector('#decks');
const error = document.querySelector('#error');
for (const [deck, label] of Object.entries(labels)) {
  const section = document.createElement('section');
  section.className = 'deck';
  section.id = deck;
  // Only fixed application strings enter this template. Remote titles use textContent.
  section.innerHTML = `<h2>${label} PPT</h2><div class="current-title">尚未選擇檔案</div>
    <p class="next-step"></p><div class="transport"><button class="take primary">選擇 PPT</button><button class="change">更換 PPT</button></div>
    <details class="advanced"><summary>其他頁面與手動連結</summary>
    <form><input type="url" required placeholder="貼上 PowerPoint / OneDrive 連結" aria-label="${label}連結"><button>開啟連結</button></form>
    <button class="office">Microsoft 登入 / 選檔</button><div class="pages"></div></details>`;
  section.querySelector('.office').onclick = () => act('office', { deck });
  section.querySelector('.change').onclick = () => act('sunday-folder', { deck });
  section.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const input = section.querySelector('input');
    if (await act('open', { deck, url: input.value.trim() })) input.value = '';
  };
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
  const flow = state.workflow;
  if (state.folderMessage) document.querySelector('#auto-status').textContent = state.folderMessage;
  document.querySelector('#schedule-status').textContent = state.schedule?.message || '每主日 10:00（Dallas）自動開始。';
  document.querySelector('[data-action="cancel-auto-start"]').disabled = ['starting', 'testing', 'live', 'ending'].includes(flow?.phase);
  const phases = { idle: '待機', preparing: '準備中', ready: '已就緒，可以開始崇拜', starting: '正在確認錄影', testing: '正在測試短錄影（保持靜音）', live: '崇拜中', ending: '正在結束', ended: '已結束', error: '需要處理' };
  document.querySelector('#workflow-status').textContent = flow ? `${phases[flow.phase]}${flow.error ? '：' + flow.error : ''}` : '待機';
  document.querySelector('#workflow-checks').textContent = flow ?
    `主電腦 Meet ${flow.hostReady ? '✓' : '待確認'} · 講員準備 ${flow.peerReady ? '✓' : (flow.peerError || '等待連線')} · 兩份簡報 ${flow.slidesReady ? '✓' : '待確認投影片'} · 錄影 ${flow.recording ? '已確認開始' : '未確認開始'}${flow.phase === 'live' ? ' · 講員收音 ' + (flow.speakerLive ? '已確認開啟' : '等待確認') : ''}` : '';
  document.querySelector('[data-action="begin-service"]').disabled = flow?.phase !== 'ready';
  document.querySelector('[data-action="prepare-service"]').disabled = ['preparing', 'starting', 'testing', 'live', 'ending'].includes(flow?.phase);
  document.querySelector('[data-action="test-recording"]').disabled = !flow?.hostReady || ['starting', 'testing', 'live', 'ending'].includes(flow?.phase);
  document.querySelector('[data-action="end-service"]').disabled = ['idle', 'ending', 'ended'].includes(flow?.phase);
  document.querySelector('[data-action="confirm-share"]').hidden = !flow?.error?.includes('OUTPUT');
  if (!state.meetingConfigured) document.querySelector('#first-settings').open = true;
  const remote = state.remote;
  document.querySelector('#remote-status').textContent = remote?.error || (remote?.paired ? '講員已連線，可以翻頁。' :
    remote?.enabled ? (remote.remembered ? '已記住講員電腦，等待自動連線。' : `請講員打開 Sunday Remote，輸入 ${remote.code}（五分鐘內）。`) : '按準備主日後，講員可用 Sunday Remote 控制上一頁／下一頁。');
  document.querySelector('#remote-start').hidden = !!remote?.enabled;
  document.querySelector('#remote-stop').hidden = !remote?.enabled;
  const website = state.website;
  const folder = website?.folder;
  document.querySelector('#sunday-date').textContent = folder ? `本主日：${folder.date} · ${folder.folderName}` : '本主日簡報';
  document.querySelector('#sunday-status').textContent = website?.status === 'loading' ? '正在向教會網站取得連結…' :
    website?.error || (folder ? (folder.folderExists ? 'OneDrive 資料夾已建立；雲端檔案以 OneDrive 同步狀態為準。' :
      '本週資料夾尚未建立；網站生成或上傳 PPT 後會建立，請稍後更新。') : '請更新連結。');
  for (const button of document.querySelectorAll('[data-folder]')) button.disabled = !folder;
  document.querySelector('[data-action="refresh-folder"]').disabled = website?.status === 'loading';
  const active = state.pages.find(page => page.id === state.active);
  document.querySelector('#live').textContent = active ? `輸出：${labels[active.deck]}` : '尚未播放 · 請先準備 PPT';
  document.querySelector('[data-action="show-output"]').disabled = !active;
  document.querySelector('#hide-panel').hidden = !active;
  for (const deck of Object.keys(labels)) {
    const label = labels[deck];
    const section = document.getElementById(deck);
    section.classList.toggle('live', active?.deck === deck);
    const chosen = state.pages.find(page => page.id === state.selected[deck]);
    const candidate = window.presenterCards.cardPage(state, deck);
    const primary = section.querySelector('.primary');
    section.querySelector('.current-title').textContent = candidate?.title || '尚未選擇檔案';
    let action = 'sunday-folder', args = { deck }, hint = '點「開啟本週 PPT」，登入後自動識別敬拜與講道。';
    let buttonLabel = '選擇 PPT', disabled = false;
    if (candidate) {
      args = { id: candidate.id };
      if (candidate.id === state.active) {
        action = 'show-output'; buttonLabel = '正在播放 · 顯示畫面'; hint = '觀眾正在看這份簡報。按「返回播放」關閉設定，PPT 繼續播放。';
      } else if (candidate.ready) {
        action = 'take'; args = { deck }; buttonLabel = `播放${label}`; hint = '畫面已確認，可以播放。';
      } else if (state.preview === candidate.id && candidate.loaded && !candidate.error && /\.pptx?\b/i.test(candidate.title)) {
        action = 'confirm-play'; buttonLabel = `確認並播放${label}`;
        hint = '請先在 PowerPoint 啟動投影片放映。確認畫面只有投影片，再按下方按鈕。';
      } else {
        action = 'prepare'; buttonLabel = candidate.error ? '查看問題／重試' : '繼續準備 PPT';
        hint = candidate.error || '打開準備畫面，在 PowerPoint 啟動投影片放映，回到設定窗口確認畫面，播放後會自動隱藏設定。';
      }
    }
    primary.textContent = buttonLabel; primary.disabled = disabled;
    primary.onclick = () => act(action, args);
    section.querySelector('.next-step').textContent = hint;
    section.querySelector('.change').textContent = candidate ? '查看 PPT' : '選擇其他檔案';
    section.querySelector('.change').onclick = () => candidate ? act('prepare', { id: candidate.id }) : act('sunday-folder', { deck });
    document.querySelector(`[data-take="${deck}"]`).disabled = !chosen?.ready;
    const pages = section.querySelector('.pages');
    const deckPages = state.pages.filter(page => page.deck === deck);
    for (const row of [...pages.children]) {
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
for (const button of document.querySelectorAll('[data-take]')) button.onclick = () => act('take', { deck: button.dataset.take });
for (const button of document.querySelectorAll('[data-folder]')) button.onclick = () => act('sunday-folder', { deck: button.dataset.folder });
document.querySelector('#meeting-settings').onsubmit = async event => {
  event.preventDefault(); const input = event.target.querySelector('input');
  if (await act('save-meeting', { url: input.value.trim() })) { input.value = ''; document.querySelector('#first-settings').open = false; }
};
window.presenter.onState(render);
act('state');
