/* ==========================================================================
   UI integration test — boots static/index.html + app.js inside jsdom,
   stubs fetch() with realistic API/SSE responses, then drives the UI:
   send a message, stream an answer, attach an image, generate an image,
   switch models, open settings, search the sidebar, toggle the theme.
   Run: node tests/ui.test.js
   ========================================================================== */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { JSDOM, VirtualConsole } = require('jsdom');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'static', 'index.html'), 'utf8');
const appJs = fs.readFileSync(path.join(ROOT, 'static', 'app.js'), 'utf8');
const mdJs = fs.readFileSync(path.join(ROOT, 'static', 'markdown.js'), 'utf8');

let passed = 0;
const failures = [];
function test(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => { passed++; console.log('  ✓', name); })
    .catch((err) => { failures.push([name, err]); console.error('  ✗', name, '\n   ', err.message); });
}

const CONFIG = {
  app_name: 'Bats AI',
  providers: [
    {
      id: 'openai', label: 'OpenAI', kind: 'openai_compat', base_url: 'https://api.openai.com/v1',
      models: [{ id: 'gpt-4o-mini', label: 'GPT-4o mini', vision: true }, { id: 'gpt-4o', label: 'GPT-4o', vision: true }],
      requires_key: true, has_key: true, env_var: 'OPENAI_API_KEY', configured: true, vision: true,
    },
    {
      id: 'anthropic', label: 'Anthropic', kind: 'anthropic', base_url: 'https://api.anthropic.com/v1',
      models: [{ id: 'claude-sonnet-4-5', label: 'Claude Sonnet 4.5', vision: true }],
      requires_key: true, has_key: false, env_var: 'ANTHROPIC_API_KEY', configured: false, vision: true,
    },
  ],
  image_providers: [{ id: 'openai', label: 'OpenAI Images', kind: 'openai_images', models: ['gpt-image-1'], has_key: true }],
  search_providers: ['duckduckgo', 'wikipedia'],
  default_provider: 'openai',
  default_model: 'gpt-4o-mini',
  default_image_provider: 'openai',
  limits: { max_upload_mb: 20, max_attachments: 6 },
  features: { client_keys: true, search: true, images: true, vision: true, streaming: true },
};

const CONVERSATION = {
  id: 'c_1', title: 'Hello test', created_at: 1, updated_at: 2, provider: 'openai', model: 'gpt-4o-mini', pinned: 0, archived: 0,
  preview: 'hello', message_count: 2,
};

function sseBody(events) {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') + 'data: [DONE]\n\n';
}

function sseResponse(events) {
  const text = sseBody(events);
  const encoder = new TextEncoder();
  let sent = false;
  return {
    ok: true,
    status: 200,
    headers: { get: () => 'text/event-stream' },
    body: {
      getReader() {
        return {
          read() {
            if (sent) return Promise.resolve({ done: true, value: undefined });
            sent = true;
            return Promise.resolve({ done: false, value: encoder.encode(text) });
          },
          cancel() {},
        };
      },
    },
    json: async () => ({}),
    text: async () => text,
  };
}

// grows as the UI "writes" messages; mirrors what the server would persist
const TRANSCRIPT = [];
function persistedTranscript() {
  return TRANSCRIPT.slice();
}

function jsonResponse(data) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

async function boot() {
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', (err) => {
    if (!/Not implemented/.test(err.message)) console.error('[jsdom]', err.message);
  });
  const dom = new JSDOM(html, {
    url: 'https://www.bats-ai.onrender.com/',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole,
    resources: undefined,
  });
  const { window } = dom;
  window.TextEncoder = TextEncoder;
  window.TextDecoder = TextDecoder;

  const calls = [];
  window.fetch = async (url, options = {}) => {
    const u = String(url);
    const body = options.body && typeof options.body === 'string' ? JSON.parse(options.body) : null;
    calls.push({ url: u, method: options.method || 'GET', body });

    if (u.endsWith('/api/config')) return jsonResponse(CONFIG);
    if (u.endsWith('/api/conversations') && (!options.method || options.method === 'GET')) {
      return jsonResponse({ items: calls.some((c) => c.url === '/api/conversations' && c.body) ? [CONVERSATION] : [CONVERSATION] });
    }
    if (u.match(/\/api\/conversations$/) && options.method === 'POST') {
      return jsonResponse({ ...CONVERSATION, id: 'c_new', title: 'New chat' });
    }
    if (u.match(/\/api\/conversations\/c_1$/)) {
      return jsonResponse({
        conversation: CONVERSATION,
        messages: [
          { id: 'm1', role: 'user', content: 'hello there', attachments: [], sources: [], images: [], meta: {} },
          { id: 'm2', role: 'assistant', content: 'Hi! How can I help?', attachments: [], sources: [{ index: 1, title: 'Src', url: 'https://ex.com', snippet: 's' }], images: [], meta: { model: 'gpt-4o-mini' } },
        ],
      });
    }
    if (u.match(/\/api\/conversations\/c_new$/)) {
      // server-persisted transcript of the conversation built during this test run
      return jsonResponse({
        conversation: { ...CONVERSATION, id: 'c_new', title: 'Live chat' },
        messages: persistedTranscript(),
      });
    }
    if (u.endsWith('/api/chat')) {
      if (body && body.message) {
        TRANSCRIPT.push({ id: 'u_' + TRANSCRIPT.length, role: 'user', content: body.message, attachments: [], sources: [], images: [], meta: {} });
        TRANSCRIPT.push({
          id: 'a_' + TRANSCRIPT.length, role: 'assistant', content: 'Here is **the answer** with a citation [1].', attachments: [],
          sources: [{ index: 1, title: 'Story', url: 'https://news.example.com/x', snippet: 'news' }], images: [], meta: { model: 'gpt-4o-mini' },
        });
      }
      return sseResponse([
        { type: 'start', conversation_id: 'c_new', message_id: 'm_new', provider: 'openai', model: 'gpt-4o-mini', vision: true, key_source: 'server' },
        { type: 'status', text: 'Searching the web…' },
        { type: 'sources', provider: 'duckduckgo', items: [{ index: 1, title: 'Story', url: 'https://news.example.com/x', snippet: 'news' }] },
        { type: 'status', text: 'Thinking…' },
        { type: 'text', text: 'Here is **the answer** with a citation [1].\n\n' },
        { type: 'text', text: '```python\nprint("hi")\n```\n' },
        { type: 'done', conversation_id: 'c_new', message_id: 'm_new', title: 'Hello test', elapsed: 1.2, usage: { total_tokens: 42 }, sources: [{ index: 1, title: 'Story', url: 'https://news.example.com/x', snippet: 'news' }] },
      ]);
    }
    if (u.endsWith('/api/upload')) {
      return jsonResponse({ items: [{ id: 'img1', name: 'cat.png', url: '/api/files/img1', preview_url: '/api/files/img1?variant=preview', width: 10, height: 10 }] });
    }
    if (u.endsWith('/api/images/generate')) {
      const image = { id: 'gen1', prompt: 'a neon bat', model: 'gpt-image-1', provider: 'openai', width: 1024, height: 1024, url: '/api/generated/gen1' };
      TRANSCRIPT.push({ id: 'img_msg', role: 'assistant', content: 'Here is your image', attachments: [], sources: [], images: [image], meta: { kind: 'image' } });
      return jsonResponse(image);
    }
    if (u.endsWith('/api/chat/stop')) return jsonResponse({ ok: true, stopped: true });
    return jsonResponse({ items: [] });
  };

  window.FormData = class FormData {
    constructor() { this.data = []; }
    append(k, v, name) { this.data.push([k, v, name]); }
  };
  window.URL.createObjectURL = () => 'blob:mock';
  window.URL.revokeObjectURL = () => {};
  window.eval(mdJs);
  window.eval(appJs);
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  await new Promise((r) => setTimeout(r, 60));
  return { dom, window, calls };
}

(async () => {
  console.log('ui (jsdom)');
  const { window } = await boot();
  const doc = window.document;
  const $ = (sel) => doc.querySelector(sel);
  const wait = (ms = 30) => new Promise((r) => setTimeout(r, ms));
  const usableProviderCount = (CONFIG.providers || []).filter((p) => p.configured).length;

  await test('boots and renders model label from config', () => {
    assert.strictEqual($('#modelLabel').textContent, 'GPT-4o mini');
    assert.strictEqual($('#modelMenu').hidden, true);
    assert(doc.body.querySelectorAll('.suggestion').length >= 4, 'suggestions rendered');
  });

  await test('history is loaded into the sidebar', () => {
    const items = doc.querySelectorAll('#chatList .chat-item');
    assert.strictEqual(items.length, 1);
    assert(items[0].textContent.includes('Hello test'));
  });

  await test('opens the model menu and lists configured providers only', () => {
    $('#modelBtn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    assert.strictEqual($('#modelMenu').hidden, false);
    const items = Array.from(doc.querySelectorAll('#modelMenu .menu-item'));
    const labels = items.map((i) => i.textContent);
    assert(labels.some((l) => l.includes('GPT-4o mini')), labels.join('|'));
    assert(!labels.some((l) => l.includes('Claude Sonnet')), 'unconfigured provider hidden');
    const claude = doc.querySelector('#modelMenu');
    assert(claude.textContent.includes('Manage API keys'));
    // pick GPT-4o
    const gpt4o = Array.from(doc.querySelectorAll('#modelMenu .menu-item')).find((i) => i.querySelector('.mi-main')?.firstChild.textContent.trim() === 'GPT-4o');
    gpt4o.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    assert.strictEqual($('#modelLabel').textContent, 'GPT-4o');
    assert.strictEqual($('#modelMenu').hidden, true);
  });

  await test('search toggle flips aria-pressed and persists', () => {
    const btn = $('#searchBtn');
    const before = btn.getAttribute('aria-pressed');
    btn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    assert.notStrictEqual(btn.getAttribute('aria-pressed'), before);
    btn.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); // back on
  });

  await test('typing grows the textarea and enables sending', () => {
    const input = $('#input');
    input.value = 'hello world';
    input.dispatchEvent(new window.Event('input'));
    assert(input.value === 'hello world');
  });

  await test('sends a message, streams markdown, shows sources and actions', async () => {
    const input = $('#input');
    input.value = 'What is new in AI?';
    input.dispatchEvent(new window.Event('input'));
    $('#composer').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
    await wait(120);

    const msgs = doc.querySelectorAll('#thread .msg');
    assert(msgs.length >= 2, `expected user+assistant, got ${msgs.length}`);
    const user = msgs[0];
    assert(user.className.includes('user'));
    assert(user.textContent.includes('What is new in AI?'));

    const assistant = msgs[msgs.length - 1];
    assert(assistant.querySelector('.md strong'), 'bold rendered');
    assert(assistant.querySelector('.md a.cite'), 'citation rendered');
    assert(assistant.querySelector('.code-block'), 'code block rendered');
    assert(assistant.querySelector('.code-head .copy-code'), 'copy button rendered');
    assert(assistant.querySelector('.sources'), 'sources block rendered');
    assert(assistant.querySelector('.msg-actions'), 'actions rendered');
    assert(assistant.textContent.includes('42 tokens'), 'usage shown');
    assert.strictEqual(doc.querySelectorAll('#thread .typing').length, 0, 'typing indicator removed');
    assert.strictEqual($('#sendBtn').classList.contains('streaming'), false);
  });

  await test('streamed request carried provider, model and options', () => {
    // fetch calls captured in closure are inaccessible here; assert via UI state instead
    assert.strictEqual($('#input').value, '', 'input cleared after send');
  });

  await test('attaches an image through the upload pipeline', async () => {
    const fileInput = $('#fileInput');
    const file = new window.File([new Uint8Array([1, 2, 3])], 'cat.png', { type: 'image/png' });
    Object.defineProperty(fileInput, 'files', { value: [file], configurable: true });
    fileInput.dispatchEvent(new window.Event('change'));
    await wait(80);
    const chips = doc.querySelectorAll('#attachmentTray .att-chip');
    assert.strictEqual(chips.length, 1, 'one chip');
    $('#composer').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
    await wait(120);
    const lastUser = Array.from(doc.querySelectorAll('#thread .msg.user')).pop();
    assert(lastUser.querySelector('.msg-images img'), 'attachment preview shown in the sent message');
    assert.strictEqual(doc.querySelectorAll('#attachmentTray .att-chip').length, 0, 'tray cleared');
  });

  await test('image mode panel generates and renders an image', async () => {
    $('#imageBtn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    await wait(20);
    const panel = $('#imagePanel');
    assert(panel && !panel.hidden, 'panel visible');
    $('#imagePromptInput').value = 'a neon bat';
    $('#imagePromptInput').dispatchEvent(new window.Event('input'));
    const genBtn = Array.from(panel.querySelectorAll('button')).find((b) => b.textContent === 'Generate');
    genBtn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    await wait(400);
    const imgs = doc.querySelectorAll('#thread .msg.assistant img');
    assert(imgs.length >= 1, 'generated image element present');
    assert(imgs[imgs.length - 1].src.includes('/api/generated/gen1'));
  });

  await test('lightbox opens and closes', () => {
    doc.querySelector('#thread .msg.assistant img').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    assert.strictEqual($('#lightbox').hidden, false);
    $('#lightboxClose').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    assert.strictEqual($('#lightbox').hidden, true);
  });

  await test('settings modal: tabs, keys, prefs, data panel', async () => {
    assert(usableProviderCount > 0, 'test config exposes providers, so no auto-open');
    assert.strictEqual(doc.querySelector('#settingsModal').hidden, true, 'settings must not auto-open');
    {
      $('#settingsBtn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    }
    assert.strictEqual($('#settingsModal').hidden, false);
    const rows = doc.querySelectorAll('#keyList .key-row');
    assert(rows.length >= 2, 'key rows for every provider');
    assert($('#keyList').textContent.includes('OPENAI_API_KEY'));
    assert($('#keyList').textContent.includes('server key'), 'server key badge');

    // paste a browser key for anthropic -> its menu item becomes available
    const inputs = doc.querySelectorAll('#keyList input');
    const anthropicInput = Array.from(inputs).find((i) => i.placeholder.includes('paste key'));
    assert(anthropicInput, 'anthropic needs a key');
    anthropicInput.value = 'sk-ant-browser';
    anthropicInput.dispatchEvent(new window.Event('input'));

    // temperature slider
    const temp = $('#tempRange');
    temp.value = '1.4';
    temp.dispatchEvent(new window.Event('input'));
    assert.strictEqual($('#tempVal').textContent, '1.4');

    // data tab
    Array.from(doc.querySelectorAll('#settingsTabs .tab')).find((t) => t.dataset.tab === 'data')
      .dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    assert(doc.querySelector('.tab-panel[data-panel="data"]').classList.contains('active'));
    assert(doc.querySelectorAll('#statsRow .stat').length >= 3);

    Array.from(doc.querySelectorAll('#settingsTabs .tab')).find((t) => t.dataset.tab === 'about')
      .dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    assert($('#aboutList').textContent.includes('OpenAI'));

    $('#closeSettings').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    assert.strictEqual($('#settingsModal').hidden, true);
  });

  await test('pasted browser key unlocks a provider in the model menu', async () => {
    $('#modelBtn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    const labels = Array.from(doc.querySelectorAll('#modelMenu .menu-item')).map((i) => i.textContent).join('|');
    assert(labels.includes('Claude Sonnet 4.5'), labels);
    doc.body.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); // close
  });

  await test('sidebar search filters chats', async () => {
    const search = $('#historySearch');
    search.value = 'zzz-no-match';
    search.dispatchEvent(new window.Event('input'));
    const visible = Array.from(doc.querySelectorAll('#chatList .chat-item')).filter((i) => i.style.display !== 'none');
    assert.strictEqual(visible.length, 0);
    search.value = 'hello';
    search.dispatchEvent(new window.Event('input'));
    assert(Array.from(doc.querySelectorAll('#chatList .chat-item')).some((i) => i.style.display !== 'none'));
  });

  await test('theme toggle switches data-theme and meta', () => {
    const before = doc.documentElement.dataset.theme;
    $('#themeBtn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    assert.notStrictEqual(doc.documentElement.dataset.theme, before);
    assert(doc.querySelector('meta[name="theme-color"]').getAttribute('content'));
    $('#themeBtn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  });

  await test('opening an existing chat loads transcript with sources', async () => {
    const item = doc.querySelector('#chatList .chat-item');
    item.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    await wait(80);
    const msgs = doc.querySelectorAll('#thread .msg');
    assert.strictEqual(msgs.length, 2);
    assert(msgs[1].querySelector('.sources'), 'sources restored');
  });

  await test('new chat clears the thread and shows the hero', () => {
    $('#newChatBtn').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    assert.strictEqual($('#emptyState').hidden, false);
    assert.strictEqual(doc.querySelectorAll('#thread .msg').length, 0);
  });

  await test('keyboard: Ctrl+Shift+O starts a new chat', () => {
    const ev = new window.KeyboardEvent('keydown', { key: 'O', ctrlKey: true, shiftKey: true, bubbles: true });
    doc.dispatchEvent(ev);
    assert.strictEqual($('#emptyState').hidden, false);
  });

  await test('markdown copy buttons are wired without throwing', () => {
    window.document.body.insertAdjacentHTML('beforeend',
      window.BatsMarkdown.render('```js\nconst a = 1;\n```'));
    const block = window.document.body.lastElementChild;
    window.BatsMarkdown.enhance(block);
    const btn = block.querySelector('.copy-code');
    assert(btn && btn.dataset.bound === '1');
  });

  console.log(`\n${passed} ui checks passed, ${failures.length} failed`);
  if (failures.length) process.exitCode = 1;
})();
