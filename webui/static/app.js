import { LitElement, html, nothing } from 'lit';
import { repeat } from 'lit/directives/repeat.js';
import '@material/web/button/filled-button.js';
import '@material/web/button/filled-tonal-button.js';
import '@material/web/button/outlined-button.js';
import '@material/web/button/text-button.js';
import '@material/web/textfield/outlined-text-field.js';
import '@material/web/select/outlined-select.js';
import '@material/web/select/select-option.js';
import '@material/web/tabs/tabs.js';
import '@material/web/tabs/primary-tab.js';
import '@material/web/switch/switch.js';
import '@material/web/dialog/dialog.js';

// Resolve against the document, including reverse-proxy prefixes such as /douyin/.
const BASE = new URL('./', document.baseURI);
const appUrl = path => new URL(path.replace(/^\/+/, ''), BASE).href;
const mediaUrl = (base, path) => appUrl(base + path.split('/').map(encodeURIComponent).join('/'));
const TABS = [['tasks', '录制任务'], ['config', '配置'], ['logs', '日志'], ['files', '录制文件']];
const STATUS = { recording: '录制中', waiting: '等待检测', offline: '未开播', error: '出错', stopped: '已暂停', unknown: '未知' };
const duration = value => {
  const sec = Math.floor(value || 0);
  return `${Math.floor(sec / 3600)}h ${Math.floor(sec % 3600 / 60)}m ${sec % 60}s`;
};
const size = value => {
  let n = value || 0, i = 0;
  while (n >= 1024 && i < 3) { n /= 1024; i++; }
  return `${n.toFixed(1)} ${['B', 'KB', 'MB', 'GB'][i]}`;
};

class RecorderApp extends LitElement {
  static properties = {
    tab: { state: true }, status: { state: true }, tasks: { state: true },
    platforms: { state: true }, qualities: { state: true }, videos: { state: true },
    logs: { state: true }, connected: { state: true }, offline: { state: true },
    lastUpdated: { state: true }, mode: { state: true }, platform: { state: true },
    query: { state: true }, filter: { state: true }, configText: { state: true },
    configOriginal: { state: true }, configLoaded: { state: true },
    busy: { state: true }, notification: { state: true },
    dialog: { state: true }, playing: { state: true }, autoScroll: { state: true },
    theme: { state: true },
  };

  constructor() {
    super();
    this.tab = 'tasks'; this.status = {}; this.tasks = []; this.platforms = [];
    this.qualities = []; this.videos = []; this.logs = []; this.connected = null;
    this.offline = !navigator.onLine; this.lastUpdated = ''; this.mode = 'platform';
    this.platform = ''; this.query = ''; this.filter = 'all'; this.configText = '';
    this.configOriginal = ''; this.configLoaded = false; this.busy = new Set();
    this.notification = null; this.dialog = null; this.playing = ''; this.autoScroll = true;
    this.requests = new Map(); this.controllers = new Set(); this.theme = 'auto';
    try { this.theme = localStorage.getItem('dlr-theme') || 'auto'; } catch { /* Storage may be disabled. */ }
    if (!['auto', 'light', 'dark'].includes(this.theme)) this.theme = 'auto';
    this.colorScheme = matchMedia('(prefers-color-scheme: dark)');
    this.onSchemeChange = () => this.applyTheme();
    this.onNetwork = () => { this.offline = !navigator.onLine; if (!this.offline) this.refresh(); };
    this.onUnload = event => {
      if (this.configDirty) { event.preventDefault(); event.returnValue = ''; }
    };
  }

  // The shell stylesheet also supplies shared Material tokens to nested components.
  createRenderRoot() { this.replaceChildren(); return this; }
  get configDirty() { return this.configText !== this.configOriginal; }
  get dark() { return this.theme === 'dark' || (this.theme === 'auto' && this.colorScheme.matches); }

  connectedCallback() {
    super.connectedCallback();
    this.applyTheme();
    this.colorScheme.addEventListener('change', this.onSchemeChange);
    window.addEventListener('online', this.onNetwork);
    window.addEventListener('offline', this.onNetwork);
    window.addEventListener('beforeunload', this.onUnload);
    this.updateComplete.then(() => { this.loadPlatforms(); this.refresh(); });
    this.poll = setInterval(() => {
      if (!document.hidden && navigator.onLine) this.refresh();
    }, 5000);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    clearInterval(this.poll); clearTimeout(this.toastTimer);
    this.colorScheme.removeEventListener('change', this.onSchemeChange);
    window.removeEventListener('online', this.onNetwork);
    window.removeEventListener('offline', this.onNetwork);
    window.removeEventListener('beforeunload', this.onUnload);
    for (const controller of this.controllers) controller.abort();
    this.resolveDialog?.(false);
  }

  applyTheme() {
    document.documentElement.dataset.theme = this.theme;
    document.querySelector('#meta-theme').content = this.dark ? '#131318' : '#f9f9ff';
    this.requestUpdate();
  }
  toggleTheme() {
    this.theme = this.dark ? 'light' : 'dark';
    try { localStorage.setItem('dlr-theme', this.theme); } catch { /* Optional persistence. */ }
    this.applyTheme();
  }
  toast(message, error = false) {
    this.notification = { message, error };
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { this.notification = null; }, 4000);
  }

  async request(path, options = {}, text = false) {
    const controller = new AbortController();
    this.controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(appUrl(path), { ...options, signal: controller.signal });
      if (!response.ok) {
        let detail = response.statusText;
        try { detail = (await response.json()).detail || detail; } catch { /* Non-JSON error. */ }
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      }
      return await (text ? response.text() : response.json());
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时或已取消');
      throw error;
    } finally { clearTimeout(timeout); this.controllers.delete(controller); }
  }

  async load(key, worker) {
    if (this.requests.has(key)) return this.requests.get(key);
    const pending = Promise.resolve().then(worker).catch(error => {
      if (key === 'status') this.connected = false;
      else this.toast(`加载失败：${error.message}`, true);
    }).finally(() => this.requests.delete(key));
    this.requests.set(key, pending);
    return pending;
  }
  async act(key, worker) {
    if (this.busy.has(key)) return;
    this.busy = new Set([...this.busy, key]);
    try { await worker(); } catch (error) { this.toast(error.message, true); }
    finally { this.busy = new Set([...this.busy].filter(x => x !== key)); }
  }
  refresh() {
    this.loadStatus();
    if (this.tab === 'tasks') this.loadTasks();
    if (this.tab === 'logs') this.loadLogs();
  }
  loadStatus() {
    return this.load('status', async () => {
      this.status = await this.request('/api/status');
      this.connected = true;
      this.lastUpdated = new Date().toLocaleTimeString();
    });
  }
  loadTasks() {
    return this.load('tasks', async () => { this.tasks = (await this.request('/api/tasks')).tasks || []; });
  }
  loadPlatforms() {
    return this.load('platforms', async () => {
      const data = await this.request('/api/platforms');
      this.platforms = data.platforms || []; this.qualities = data.qualities || [];
      await this.updateComplete;
      if (!this.platform && this.platforms.some(p => p.name === '抖音直播' && p.url_template)) {
        this.platform = '抖音直播';
      }
    });
  }
  async selectTab(tab) {
    this.tab = tab;
    if (tab !== 'files') this.querySelector('#player')?.pause();
    if (tab === 'config' && !this.configLoaded) this.loadConfig();
    if (tab === 'logs') this.loadLogs();
    if (tab === 'files') this.loadVideos();
  }
  value(id) { return this.querySelector(`#${id}`)?.value?.trim() || ''; }

  async addTask(event) {
    event.preventDefault();
    await this.act('add', async () => {
      const body = { quality: this.value('new-quality'), name: this.value('new-name') };
      const byId = this.mode === 'platform';
      if (byId) {
        body.platform = this.platform; body.id = this.value('new-id');
        if (!body.platform || !body.id) throw new Error('请选择平台并输入直播间 ID / 用户名');
      } else {
        body.url = this.value('new-url');
        if (!body.url) throw new Error('请输入直播间 URL');
      }
      await this.request(byId ? '/api/tasks/from-id' : '/api/tasks', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      for (const id of ['new-id', 'new-url']) {
        const field = this.querySelector(`#${id}`); if (field) field.value = '';
      }
      this.toast('任务已添加');
      await Promise.all([this.loadTasks(), this.loadStatus()]);
    });
  }
  async toggleTask(task) {
    await this.act(task.url, async () => {
      await this.request(`/api/tasks/comment?url=${encodeURIComponent(task.url)}&commented=${!task.commented}`, { method: 'PUT' });
      this.toast(task.commented ? '任务已恢复' : '任务已暂停');
      await Promise.all([this.loadTasks(), this.loadStatus()]);
    });
  }
  async removeTask(task) {
    if (!await this.confirm('删除该任务？', `${task.url}\n\n录制中的任务会立即停止。`, '删除')) return;
    await this.act(task.url, async () => {
      await this.request(`/api/tasks?url=${encodeURIComponent(task.url)}`, { method: 'DELETE' });
      this.toast('任务已删除');
      await Promise.all([this.loadTasks(), this.loadStatus()]);
    });
  }
  async confirm(title, message, action = '确认') {
    if (this.resolveDialog) return false;
    this.dialog = { title, message, action };
    const answer = new Promise(resolve => { this.resolveDialog = resolve; });
    await this.updateComplete;
    const dialog = this.querySelector('md-dialog');
    dialog.returnValue = 'cancel';
    dialog.show();
    return answer;
  }
  finishDialog(event) {
    const accepted = event.currentTarget.returnValue === 'confirm';
    this.resolveDialog?.(accepted); this.resolveDialog = null;
  }
  async loadConfig() {
    if (this.configDirty && !await this.confirm('重新载入配置？', '当前配置尚未保存，重新载入将丢失未保存的更改。', '重新载入')) return;
    return this.load('config', async () => {
      this.configText = this.configOriginal = await this.request('/api/config', {}, true);
      this.configLoaded = true;
    });
  }
  saveConfig() {
    return this.act('save', async () => {
      // Keep edits made while the save request is in flight marked as unsaved.
      const snapshot = this.configText;
      await this.request('/api/config', { method: 'PUT', headers: { 'Content-Type': 'text/plain' }, body: snapshot });
      this.configOriginal = snapshot;
      this.toast('配置已保存，新开任务自动生效');
    });
  }
  loadLogs() {
    return this.load('logs', async () => {
      const box = this.querySelector('#log-box');
      const atBottom = box && box.scrollHeight - box.scrollTop - box.clientHeight < 48;
      this.logs = (await this.request('/api/logs?limit=300')).logs || [];
      await this.updateComplete;
      if (box && (this.autoScroll || atBottom)) box.scrollTop = box.scrollHeight;
    });
  }
  loadVideos() {
    return this.load('videos', async () => {
      this.videos = ((await this.request('/api/videos')).files || []).sort((a, b) => b.mtime - a.mtime);
    });
  }
  async playVideo(path) {
    this.selectTab('files'); this.playing = path;
    await this.updateComplete;
    const player = this.querySelector('#player');
    player.pause(); player.src = mediaUrl('/api/videos/play/', path); player.load();
    player.play().catch(() => { /* Browser may require another user gesture. */ });
    player.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  renderDeploy() {
    const d = this.status.deploy;
    if (!d || ['no_manifest', 'unknown'].includes(d.state)) return nothing;
    let message = '';
    if (d.state === 'unverified') message = '安装目录的部署清单已丢失，无法校验代码版本；请重新执行 deploy/install.sh 重建清单';
    else if (d.state === 'skew') message = `部署校验失败：${d.skew_count} 个代码文件与安装清单不一致，请重新部署并重启服务。`;
    else if (d.state === 'extra') message = `部署目录存在 ${d.extra_count} 个清单外文件，建议重新部署清理。`;
    else if (d.dirty) message = '当前部署来自含未提交改动的源码目录。';
    return message ? html`<div class="banner ${d.dirty ? 'warn' : ''}" role="alert">${message}</div>` : nothing;
  }
  option(value, label = value) {
    return html`<md-select-option value=${value}><div slot="headline">${label}</div></md-select-option>`;
  }
  renderTasks() {
    const platform = this.platforms.find(p => p.name === this.platform);
    const filtered = this.tasks.filter(task => {
      const status = task.commented ? 'stopped' : task.status || 'unknown';
      return (this.filter === 'all' || this.filter === status ||
        (this.filter === 'waiting' && ['waiting', 'offline', 'unknown'].includes(status))) &&
        [task.anchor, task.name, task.platform, task.url].join(' ').toLowerCase().includes(this.query.toLowerCase().trim());
    });
    return html`
      <section class="sheet" aria-label="添加录制任务">
        <h2>添加直播间</h2><p class="muted">支持 ${this.platforms.length} 个平台，${this.platforms.filter(p => p.url_template).length} 个支持 ID 快捷添加</p>
        <form @submit=${this.addTask}>
          <div class="form-row">
            <md-outlined-select label="添加方式" .value=${this.mode} @change=${e => { this.mode = e.target.value; }}>
              ${this.option('platform', '选平台 + ID')}${this.option('url', '粘贴完整网址')}
            </md-outlined-select>
            <md-outlined-select id="new-platform" label="直播平台" ?hidden=${this.mode !== 'platform'} .value=${this.platform}
              @change=${e => { this.platform = e.target.value; }}>
              ${this.option('', '请选择平台')}${this.platforms.filter(p => p.url_template).map(p => this.option(p.name))}
            </md-outlined-select>
            <md-outlined-text-field id="new-id" class="room-field" label="直播间 ID / 用户名" ?hidden=${this.mode !== 'platform'}
              placeholder=${platform?.id_placeholder || '输入直播间 ID / 用户名'}></md-outlined-text-field>
            <md-outlined-text-field id="new-url" class="room-field" type="url" label="直播间 URL" ?hidden=${this.mode !== 'url'}
              placeholder="https://live.douyin.com/123456"></md-outlined-text-field>
          </div>
          <div class="form-row">
            <md-outlined-select id="new-quality" label="录制画质">${this.option('', '默认画质')}${this.qualities.map(q => this.option(q))}</md-outlined-select>
            <md-outlined-text-field id="new-name" class="note-field" label="备注（可选）"></md-outlined-text-field>
            <md-filled-button id="add-task" type="submit" ?disabled=${this.offline || this.busy.has('add')}>${this.busy.has('add') ? '添加中…' : '添加任务'}</md-filled-button>
          </div>
        </form>
      </section>
      <section class="sheet" aria-label="任务列表">
        <div class="toolbar">
          <md-outlined-text-field id="task-search" class="search-field" type="search" label="搜索主播、平台或 URL" .value=${this.query}
            @input=${e => { this.query = e.target.value; }}></md-outlined-text-field>
          <md-outlined-select label="筛选任务状态" .value=${this.filter} @change=${e => { this.filter = e.target.value; }}>
            ${[['all', '全部状态'], ['recording', '录制中'], ['waiting', '等待 / 未开播'], ['stopped', '已暂停'], ['error', '出错']].map(([v, l]) => this.option(v, l))}
          </md-outlined-select>
          <md-filled-tonal-button @click=${() => { this.loadTasks(); this.loadStatus(); }}>刷新</md-filled-tonal-button>
          <span class="summary muted">显示 ${filtered.length} / ${this.tasks.length} 个任务</span>
        </div>
        <div class="table-scroll"><table>
          <thead><tr>${['状态', '主播', '平台', '画质', '已录制', 'URL', '操作'].map(label => html`<th scope="col">${label}</th>`)}</tr></thead>
          <tbody>${filtered.length ? repeat(filtered, t => t.url, t => this.renderTask(t)) : html`<tr><td colspan="7" class="empty">${this.tasks.length ? '没有符合筛选条件的任务' : '暂无任务，请在上方添加直播间'}</td></tr>`}</tbody>
        </table></div>
      </section>`;
  }
  renderTask(task) {
    const status = task.commented ? 'stopped' : Object.hasOwn(STATUS, task.status) ? task.status : 'unknown';
    const disabled = this.offline || this.busy.has(task.url);
    return html`<tr>
      <td data-label="状态"><span class="chip ${status}">${STATUS[status]}</span></td>
      <td data-label="主播"><div class="anchor"><b>${task.anchor || task.name || '-'}</b>${status === 'error' ? html`<small class="dirty">${task.message}</small>` : nothing}</div></td>
      <td data-label="平台">${task.platform || '-'}</td><td data-label="画质">${task.quality || '-'}</td>
      <td data-label="已录制">${task.recording_seconds ? duration(task.recording_seconds) : '-'}</td>
      <td data-label="URL"><div class="url" title=${task.url}>${task.url}</div></td>
      <td data-label="操作"><div class="row-actions">
        <md-text-button ?disabled=${disabled} @click=${() => this.toggleTask(task)}>${task.commented ? '恢复' : '暂停'}</md-text-button>
        <md-text-button class="danger" ?disabled=${disabled} @click=${() => this.removeTask(task)}>删除</md-text-button>
        ${task.file ? html`<md-text-button @click=${() => this.playVideo(task.file)}>播放</md-text-button>` : nothing}
      </div></td>
    </tr>`;
  }
  renderConfig() {
    return html`<section class="sheet"><h2>录制配置</h2>
      <p class="${this.configDirty ? 'dirty' : 'muted'}">${this.configDirty ? '● 有未保存的更改' : '保存后无需重启：录制进程每轮值守自动重读配置'}</p>
      <div class="form-row">
        <md-filled-button id="save-config" ?disabled=${!this.configLoaded || this.busy.has('save') || this.offline} @click=${this.saveConfig}>保存配置</md-filled-button>
        <md-outlined-button id="reload-config" ?disabled=${this.busy.has('save') || this.offline} @click=${this.loadConfig}>重新载入</md-outlined-button>
      </div>
      <textarea id="config-text" class="editor" spellcheck="false" aria-label="配置文本" ?disabled=${!this.configLoaded}
        .value=${this.configText} @input=${e => { this.configText = e.target.value; }}></textarea>
    </section>`;
  }
  renderLogs() {
    return html`<section class="sheet"><h2>运行日志</h2><p class="muted">最近的录制与系统事件</p>
      <div class="toolbar"><md-filled-tonal-button @click=${this.loadLogs}>刷新</md-filled-tonal-button>
        <label class="switch-label"><md-switch aria-label="自动滚动" .selected=${this.autoScroll} @change=${e => { this.autoScroll = e.target.selected; }}></md-switch>自动滚动</label>
        <span class="muted">${this.logs.length} 条</span>
      </div>
      <div class="logs" id="log-box" role="log" aria-live="off">${this.logs.length ? this.logs.map(log => html`
        <div class="log-row log-${log.level}"><span>${log.level || 'INFO'}</span><span>${new Date(log.time * 1000).toLocaleTimeString()}</span><span>${log.message}</span></div>`) : html`<div class="empty">暂无日志</div>`}</div>
    </section>`;
  }
  renderFiles() {
    const groups = new Map();
    for (const file of this.videos) {
      const dir = file.path.includes('/') ? file.path.slice(0, file.path.lastIndexOf('/')) : '根目录';
      if (!groups.has(dir)) groups.set(dir, []);
      groups.get(dir).push(file);
    }
    return html`<section class="sheet"><h2>录制文件</h2><p class="muted">播放或下载已归档的录制内容</p>
      <md-filled-tonal-button @click=${this.loadVideos}>刷新</md-filled-tonal-button>
      <video id="player" ?hidden=${!this.playing} controls preload="metadata" @error=${() => {
        if (this.playing) this.toast('播放失败，请检查文件是否完整、浏览器编解码支持及服务端 ffmpeg', true);
      }}></video>
      ${this.playing ? html`<p class="muted">${this.playing}</p>` : nothing}
      ${groups.size ? [...groups].map(([dir, files]) => html`<h3 class="group-title">${dir} · ${files.length}</h3>
        ${files.map(file => html`<div class="file-item">
          <a class="path" href=${mediaUrl('/api/videos/play/', file.path)} @click=${e => { e.preventDefault(); this.playVideo(file.path); }}>${file.name}</a>
          <span class="muted">${size(file.size)}</span>
          <md-outlined-button href=${mediaUrl('/videos/', file.path)} download=${file.name}>下载</md-outlined-button>
        </div>`)}`) : html`<div class="empty">暂无录制文件</div>`}
    </section>`;
  }
  render() {
    const s = this.status;
    return html`
      ${this.offline ? html`<div class="banner warn" role="status">当前处于离线状态，显示最近缓存的数据</div>` : nothing}
      ${this.renderDeploy()}
      <header class="appbar"><div class="appbar-inner">
        <div class="brand"><h1>录制管理 <span class="sub">${s.version || ''}</span></h1><div class="sub address">${BASE.origin}${BASE.pathname}</div></div>
        <span id="connection" class="chip ${this.connected === false ? 'offline' : this.connected ? 'online' : ''}">${this.connected === null ? '连接中…' : this.connected === false ? '连接失败' : s.recording_count ? '录制中' : '服务正常'}</span>
        <md-text-button id="theme-toggle" aria-label="切换明暗主题" @click=${this.toggleTheme}>${this.dark ? '浅色' : '深色'}</md-text-button>
      </div></header>
      <main class="wrap">
        <div class="stats">${[[s.recording_count, '正在录制'], [s.task_count, '录制任务'], [s.disk_free_gb, '剩余空间 / GB'], [s.platform_count, '支持平台']].map(([value, label]) => html`<div class="stat"><div class="num">${value ?? '—'}</div><div>${label}</div></div>`)}</div>
        <md-tabs aria-label="管理导航" .activeTabIndex=${TABS.findIndex(([id]) => id === this.tab)} @change=${e => this.selectTab(TABS[e.target.activeTabIndex][0])}>
          ${TABS.map(([id, label]) => html`<md-primary-tab id="tab-${id}" aria-controls="panel-${id}">${label}</md-primary-tab>`)}
        </md-tabs>
        <div id="panel-tasks" role="tabpanel" aria-labelledby="tab-tasks" ?hidden=${this.tab !== 'tasks'}>${this.renderTasks()}</div>
        <div id="panel-config" role="tabpanel" aria-labelledby="tab-config" ?hidden=${this.tab !== 'config'}>${this.renderConfig()}</div>
        <div id="panel-logs" role="tabpanel" aria-labelledby="tab-logs" ?hidden=${this.tab !== 'logs'}>${this.renderLogs()}</div>
        <div id="panel-files" role="tabpanel" aria-labelledby="tab-files" ?hidden=${this.tab !== 'files'}>${this.renderFiles()}</div>
        <div class="muted">${this.lastUpdated ? `更新于 ${this.lastUpdated}` : ''}</div>
      </main>
      <div role="status" aria-live="polite">${this.notification ? html`<div class="toast ${this.notification.error ? 'error' : ''}">${this.notification.message}</div>` : nothing}</div>
      <md-dialog aria-label=${this.dialog?.title || '确认操作'} @closed=${this.finishDialog}>
        <div slot="headline">${this.dialog?.title}</div>
        <div slot="content" class="dialog-content">${this.dialog?.message}</div>
        <div slot="actions">
          <md-text-button autofocus @click=${() => this.querySelector('md-dialog').close('cancel')}>取消</md-text-button>
          <md-filled-button @click=${() => this.querySelector('md-dialog').close('confirm')}>${this.dialog?.action || '确认'}</md-filled-button>
        </div>
      </md-dialog>`;
  }
}
customElements.define('recorder-app', RecorderApp);
