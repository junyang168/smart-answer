// No Electron import: the switching rules can also be exercised with fake views.
class Stage {
  constructor(output, preparation, changed = () => {}) {
    this.output = output;
    this.preparation = preparation;
    this.changed = changed;
    this.pages = new Map();
    this.selected = { worship: null, sermon: null };
    this.active = null;
    this.preview = null;
  }

  get(id) {
    const page = this.pages.get(Number(id));
    if (!page || page.view.webContents.isDestroyed()) throw Error('頁面已關閉，請重新載入。');
    return page;
  }

  add(deck, view, { select = true } = {}) {
    if (!Object.hasOwn(this.selected, deck)) throw Error('Unknown deck');
    const page = { id: view.webContents.id, deck, view, host: null, ready: false,
      loaded: false, error: '', title: '載入中…' };
    this.pages.set(page.id, page);
    if (select) this.selected[deck] = page.id;
    view.webContents.setAudioMuted(true);
    this.move(page, this.output);
    view.setVisible(false);
    this.changed();
    return page;
  }

  move(page, host) {
    if (page.host === host) return;
    page.view.setVisible(false);
    if (page.host) page.host.contentView.removeChildView(page.view);
    host.contentView.addChildView(page.view);
    page.host = host;
    this.layout();
  }

  layout() {
    // Keep the same viewport in both hosts; Office handles the deck's own ratio.
    const [width, height] = this.output.getContentSize();
    for (const page of this.pages.values()) page.view.setBounds({ x: 0, y: 0, width, height });
    this.preparation.setContentSize(width, height);
  }

  prepare(id) {
    const page = this.get(id);
    if (this.active === page.id) this.black();
    if (this.preview !== null) {
      const previous = this.pages.get(this.preview);
      if (previous) this.move(previous, this.output);
    }
    // Inspecting a page revokes its previous audience designation.
    page.ready = false;
    this.selected[page.deck] = page.id;
    this.preview = page.id;
    this.move(page, this.preparation);
    page.view.setVisible(true);
    this.preparation.show();
    page.view.webContents.focus();
    this.changed();
  }

  arm(id) {
    const page = this.get(id);
    if (this.preview !== page.id || !page.loaded || page.error || page.view.webContents.isLoading()) {
      throw Error('先在準備視窗檢查載入完成的觀眾投影片頁面。');
    }
    page.ready = true;
    this.preview = null;
    this.move(page, this.output);
    this.preparation.hide();
    this.changed();
  }

  take(deck) {
    const page = this.get(this.selected[deck]);
    if (!page.ready || !page.loaded || page.error) throw Error('請先檢查投影片，並按「設為觀眾畫面」。');
    // Mute every page, including editors and auth/presentation popups, first.
    this.black(false, false);
    this.move(page, this.output);
    this.active = page.id;
    page.view.setVisible(true);
    page.view.webContents.setAudioMuted(false);
    page.view.webContents.focus();
    this.output.show();
    this.changed();
  }

  black(emit = true, hideOutput = true) {
    for (const page of this.pages.values()) {
      if (!page.view.webContents.isDestroyed()) page.view.webContents.setAudioMuted(true);
      if (page.host === this.output) page.view.setVisible(false);
    }
    this.active = null;
    if (hideOutput) this.output.hide();
    if (emit) this.changed();
  }

  invalidate(id, error = '') {
    const page = this.pages.get(id);
    if (!page) return;
    if (this.active === id) this.black(false);
    page.ready = false;
    page.error = error;
    this.changed();
  }

  remove(id) {
    const page = this.pages.get(id);
    if (!page) return;
    this.invalidate(id);
    if (this.preview === id) { this.preview = null; this.preparation.hide(); }
    page.host.contentView.removeChildView(page.view);
    this.pages.delete(id);
    if (this.selected[page.deck] === id) this.selected[page.deck] = null;
    this.changed();
  }

  snapshot() {
    return { active: this.active, preview: this.preview, selected: this.selected,
      pages: [...this.pages.values()].map(({ id, deck, title, ready, loaded, error, view }) =>
        ({ id, deck, title, ready, loaded, error,
          muted: view.webContents.isDestroyed() || view.webContents.isAudioMuted() })) };
  }
}

function allowedURL(value, testOrigin) {
  try {
    const url = new URL(value);
    return !!(!url.username && !url.password &&
      (url.protocol === 'https:' || value === 'about:blank' ||
        (testOrigin && url.origin === testOrigin && url.protocol === 'http:')));
  } catch { return false; }
}

module.exports = { Stage, allowedURL };
