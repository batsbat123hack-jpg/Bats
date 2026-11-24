/* ==========================================================================
   Bats AI — front end
   Vanilla JS, no build step. Talks to the FastAPI backend over /api/*.
   ========================================================================== */
(() => {
  'use strict';

  /* ------------------------------------------------------------- helpers */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'html') node.innerHTML = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
  };
  const uid = () => Math.random().toString(36).slice(2, 10);

  const LS = {
    get(key, fallback) {
      try { const raw = localStorage.getItem('bats.' + key); return raw === null ? fallback : JSON.parse(raw); }
      catch (e) { return fallback; }
    },
    set(key, value) { try { localStorage.setItem('bats.' + key, JSON.stringify(value)); } catch (e) {} },
    del(key) { try { localStorage.removeItem('bats.' + key); } catch (e) {} },
  };

  const state = {
    config: null,
    conversations: [],
    conversationId: null,
    messages: [],
    provider: '',
    model: '',
    imageProvider: '',
    searchOn: LS.get('searchOn', true),
    streaming: false,
    controller: null,
    clientId: 'client-' + uid(),
    keys: LS.get('keys', {}),
    accessCode: LS.get('accessCode', ''),
    prefs: LS.get('prefs', { temperature: 0.7, maxTokens: 2048, imageSize: '1024x1024' }),
    pendingAttachments: [],
    uploading: 0,
    imageMode: false,
    imagePrompt: '',
    lightboxPrompt: '',
    settingsTab: 'keys',
  };

  /* --------------------------------------------------------------- toasts */
  function toast(message, kind = '', ms = 3600) {
    const node = el('div', { class: 'toast ' + kind, text: message });
    $('#toasts').append(node);
    setTimeout(() => {
      node.style.transition = 'opacity .2s, transform .2s';
      node.style.opacity = '0';
      node.style.transform = 'translateY(6px)';
      setTimeout(() => node.remove(), 220);
    }, ms);
  }

  /* ----------------------------------------------------------------- api */
  function authHeaders(extra = {}) {
    const headers = { 'Content-Type': 'application/json', ...extra };
    if (state.accessCode) headers['X-Bats-Code'] = state.accessCode;
    return headers;
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: authHeaders(options.headers || {}),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { const data = await res.json(); detail = data.detail || data.message || detail; } catch (e) {}
      const error = new Error(detail);
      error.status = res.status;
      if (res.status === 401 && /access code/i.test(detail)) {
        toast('This site needs an access code', 'error', 5000);
        openSettings('access');
      } else if (res.status === 429) {
        toast(detail || 'Slow down a little', 'error', 4000);
      }
      throw error;
    }
    const type = res.headers.get('content-type') || '';
    return type.includes('application/json') ? res.json() : res.text();
  }

  function currentKey(providerId) {
    const entry = (state.config?.providers || []).find((p) => p.id === providerId);
    if (!entry) return '';
    if (entry.has_key) return '';
    return state.keys[entry.env_var] || '';
  }
  function currentBaseUrl(providerId) {
    const entry = (state.config?.providers || []).find((p) => p.id === providerId);
    if (!entry || entry.id !== 'custom') return '';
    return state.keys['CUSTOM_BASE_URL'] || '';
  }

  /* -------------------------------------------------------------- models */
  function providerEntry(id) {
    return (state.config?.providers || []).find((p) => p.id === id);
  }
  function modelInfo(providerId, modelId) {
    const entry = providerEntry(providerId);
    return (entry?.models || []).find((m) => m.id === modelId);
  }
  function usableProviders() {
    const list = state.config?.providers || [];
    return list.filter((p) => p.free || p.configured || state.keys[p.env_var] || (p.id === 'custom' && state.keys['CUSTOM_BASE_URL']));
  }

  function pickDefaultModel() {
    const cfg = state.config;
    if (!cfg) return;
    const providers = usableProviders();
    const stored = LS.get('selection', null);
    if (stored && providers.some((p) => p.id === stored.provider)) {
      state.provider = stored.provider;
      const entry = providerEntry(stored.provider);
      const models = entry?.models || [];
      state.model = models.some((m) => m.id === stored.model)
        ? stored.model
        : (models[0]?.id || '');
      return;
    }
    if (cfg.default_provider && providers.some((p) => p.id === cfg.default_provider)) {
      state.provider = cfg.default_provider;
      state.model = cfg.default_model || providerEntry(cfg.default_provider)?.models?.[0]?.id || '';
    } else if (providers.length) {
      state.provider = providers[0].id;
      state.model = providers[0].models?.[0]?.id || '';
    } else {
      state.provider = cfg.providers?.[0]?.id || 'openai';
      state.model = cfg.providers?.[0]?.models?.[0]?.id || '';
    }
  }

  function saveSelection() {
    LS.set('selection', { provider: state.provider, model: state.model });
  }

  function renderModelButton() {
    const entry = providerEntry(state.provider);
    const info = modelInfo(state.provider, state.model);
    const label = info?.label || state.model || entry?.label || 'Select model';
    $('#modelLabel').textContent = label;
  }

  function renderModelMenu() {
    const menu = $('#modelMenu');
    menu.innerHTML = '';
    const providers = usableProviders();
    if (!providers.length) {
      menu.append(el('div', { class: 'menu-group', text: 'Free models are loading' }));
      menu.append(el('div', {
        class: 'menu-item',
        html: 'Bats Free needs no key. If nothing appears, reload the page.',
      }));
      return;
    }
    providers.forEach((provider) => {
      menu.append(el('div', { class: 'menu-group', text: provider.label }));
      (provider.models || []).forEach((model) => {
        if (!model.id) return;
        const selected = provider.id === state.provider && model.id === state.model;
        menu.append(el('button', {
          class: 'menu-item' + (selected ? ' selected' : ''),
          type: 'button',
          role: 'option',
          'aria-selected': selected ? 'true' : 'false',
          onclick: () => {
            state.provider = provider.id;
            state.model = model.id;
            saveSelection();
            renderModelButton();
            closeMenus();
            renderStatus();
          },
        },
          el('div', { class: 'mi-main' },
            model.label || model.id,
            el('span', { class: 'mi-sub', text: provider.label + (model.vision ? ' · vision' : '') }),
          ),
          provider.free ? el('span', { class: 'badge free-badge', text: 'free' }) : null,
          model.vision ? el('span', { class: 'badge', text: 'vision' }) : null,
        ));
      });
    });
    menu.append(el('div', { class: 'menu-divider' }));
    menu.append(el('button', {
      class: 'menu-item', type: 'button',
      onclick: () => { closeMenus(); openSettings('keys'); },
      html: '<span class="mi-main">Optional: add your own key<span class="mi-sub">Free models work without one</span></span>',
    }));
  }

  function closeMenus() {
    $('#modelMenu').hidden = true;
    $('#modelBtn').setAttribute('aria-expanded', 'false');
  }

  /* --------------------------------------------------------------- status */
  function renderStatus() {
    const heroSub = document.querySelector('.hero-sub');
    if (heroSub) {
      heroSub.textContent = usableProviders().length
        ? 'Free to use, no key needed: chat, live web search, image reading, image creation.'
        : 'Free models are starting up — give it a second and type below.';
    }
    const pill = $('#statusPill');
    const side = $('#sideStatus');
    const providers = usableProviders();
    const search = state.config?.search_providers || [];
    if (!providers.length) {
      pill.hidden = false;
      pill.className = 'pill error';
      pill.textContent = 'Connecting…';
      side.innerHTML = '<span class="dot warn"></span> Loading models…';
    } else if (state.streaming) {
      pill.hidden = false;
      pill.className = 'pill';
      pill.textContent = 'Generating…';
      side.innerHTML = '<span class="dot ok"></span> Connected';
    } else {
      pill.hidden = true;
      const entry = providerEntry(state.provider);
      const source = entry?.free ? 'Free · no key'
        : entry?.has_key ? 'Server key'
        : state.keys[entry?.env_var] ? 'Your key' : 'No key';
      side.innerHTML = `<span class="dot ok"></span> ${source} · ${search.length ? search[0] + ' search' : 'no search'}`;
    }
    $('#searchBtn').setAttribute('aria-pressed', state.searchOn ? 'true' : 'false');
    $('#imageBtn').setAttribute('aria-pressed', state.imageMode ? 'true' : 'false');
  }

  /* ------------------------------------------------------------ sidebar */
  function groupLabel(ts) {
    const now = new Date();
    const d = new Date(ts * 1000);
    const dayMs = 86400000;
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (d.getTime() >= startOfToday) return 'Today';
    if (d.getTime() >= startOfToday - dayMs) return 'Yesterday';
    if (d.getTime() >= startOfToday - 7 * dayMs) return 'Previous 7 days';
    if (d.getTime() >= startOfToday - 30 * dayMs) return 'Previous 30 days';
    return 'Older';
  }

  function renderChatList() {
    const list = $('#chatList');
    list.innerHTML = '';
    if (!state.conversations.length) {
      list.append(el('div', { class: 'chat-group-label', text: 'No chats yet' }));
      return;
    }
    let lastGroup = '';
    state.conversations.forEach((conv) => {
      const group = conv.pinned ? 'Pinned' : groupLabel(conv.updated_at);
      if (group !== lastGroup) {
        list.append(el('div', { class: 'chat-group-label', text: group }));
        lastGroup = group;
      }
      const active = conv.id === state.conversationId;
      const item = el('div', {
        class: 'chat-item' + (active ? ' active' : ''),
        dataset: { id: conv.id },
        onclick: (ev) => { if (!ev.target.closest('.row-actions')) openConversation(conv.id); },
      },
        conv.pinned ? el('span', { class: 'pin', html: '&#9733;' }) : null,
        el('span', { class: 'title', text: conv.title || 'New chat', title: conv.title || '' }),
        el('div', { class: 'row-actions' },
          el('button', {
            title: conv.pinned ? 'Unpin' : 'Pin', 'aria-label': 'Pin chat',
            onclick: async (ev) => {
              ev.stopPropagation();
              await api(`/api/conversations/${conv.id}`, { method: 'PATCH', body: JSON.stringify({ pinned: !conv.pinned }) });
              await refreshConversations();
            }, html: '&#9733;',
          }),
          el('button', {
            title: 'Rename', 'aria-label': 'Rename chat',
            onclick: (ev) => { ev.stopPropagation(); renameChat(conv); },
            html: '&#9998;',
          }),
          el('button', {
            title: 'Delete', 'aria-label': 'Delete chat',
            onclick: async (ev) => {
              ev.stopPropagation();
              if (!confirm(`Delete “${conv.title}”?`)) return;
              await api(`/api/conversations/${conv.id}`, { method: 'DELETE' });
              if (state.conversationId === conv.id) newChat();
              await refreshConversations();
            }, html: '&#128465;',
          }),
        ),
      );
      list.append(item);
    });
  }

  async function renameChat(conv) {
    const title = prompt('Rename chat', conv.title || 'New chat');
    if (!title) return;
    await api(`/api/conversations/${conv.id}`, { method: 'PATCH', body: JSON.stringify({ title }) });
    await refreshConversations();
    if (state.conversationId === conv.id) $('#modelLabel').textContent = modelInfo(state.provider, state.model)?.label || state.model;
  }

  async function refreshConversations() {
    try {
      const data = await api('/api/conversations');
      state.conversations = data.items || [];
      renderChatList();
    } catch (e) { /* offline */ }
  }

  /* -------------------------------------------------------------- thread */
  function scrollToBottom(smooth = true) {
    const thread = $('#thread');
    if (typeof thread.scrollTo === 'function') {
      thread.scrollTo({ top: thread.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    } else {
      thread.scrollTop = thread.scrollHeight;  // older engines / jsdom
    }
  }

  function nearBottom() {
    const t = $('#thread');
    return t.scrollHeight - t.scrollTop - t.clientHeight < 140;
  }

  function showEmpty(show) {
    $('#emptyState').hidden = !show;
    $('#thread').style.display = show ? 'none' : '';
  }

  function clearThread() {
    const thread = $('#thread');
    thread.innerHTML = '';
    const inner = el('div', { class: 'thread-inner' });
    thread.append(inner);
    return inner;
  }

  function threadInner() {
    let inner = $('#thread .thread-inner');
    if (!inner) inner = clearThread();
    return inner;
  }

  function renderMessage(msg, opts = {}) {
    const isUser = msg.role === 'user';
    const wrap = el('div', { class: 'msg ' + msg.role, dataset: { id: msg.id || uid() } });
    const avatar = el('div', {
      class: 'avatar',
      text: isUser ? (state.userInitial || 'Y') : 'B',
      title: isUser ? 'You' : 'Bats AI',
    });
    const body = el('div', { class: 'msg-body' });
    const nameRow = el('div', { class: 'msg-name' },
      isUser ? 'You' : 'Bats AI',
      !isUser && msg.meta?.model ? el('span', { class: 'meta', text: msg.meta.model }) : null,
      !isUser && msg.meta?.usage?.total_tokens ? el('span', { class: 'meta', text: `${msg.meta.usage.total_tokens} tokens` }) : null,
      !isUser && msg.meta?.elapsed ? el('span', { class: 'meta', text: `${msg.meta.elapsed}s` }) : null,
    );
    body.append(nameRow);

    const content = el('div', { class: 'md' });

    // attachments (user)
    const atts = msg.attachments || [];
    if (atts.length) {
      const grid = el('div', { class: 'msg-images' });
      atts.forEach((att) => {
        const img = el('img', { src: att.preview_url || att.url, alt: att.name || 'attachment', loading: 'lazy' });
        img.addEventListener('click', () => openLightbox(att.url, att.name || '', ''));
        grid.append(el('figure', {}, img));
      });
      body.append(grid);
    }

    if (msg.content) {
      if (isUser) {
        content.append(el('div', { class: 'bubble-text', text: msg.content }));
      } else {
        content.innerHTML = window.BatsMarkdown.render(msg.content);
        window.BatsMarkdown.enhance(content);
      }
    }
    body.append(content);

    // generated images (assistant)
    if (msg.images && msg.images.length) {
      const grid = el('div', { class: 'msg-images' });
      msg.images.forEach((image) => {
        const img = el('img', { src: image.url, alt: image.prompt || 'generated image', loading: 'lazy' });
        img.addEventListener('click', () => openLightbox(image.url, image.prompt || '', image.prompt || ''));
        grid.append(el('figure', {},
          img,
          el('div', { class: 'img-actions' },
            el('a', { href: image.url, download: `bats-${image.id}.png`, text: 'Download' }),
            el('button', {
              type: 'button', text: 'Copy prompt',
              onclick: () => window.BatsMarkdown.copyText(image.prompt || '').then(() => toast('Prompt copied', 'ok', 1600)),
            }),
            el('button', {
              type: 'button', text: 'Variation',
              onclick: () => { setImageMode(true); state.imagePrompt = image.prompt || ''; renderImagePanel(); $('#input').focus(); },
            }),
          ),
        ));
      });
      body.append(grid);
    }

    // sources
    const sources = msg.sources || msg.meta?.sources_list || [];
    if (sources.length) body.append(renderSources(sources));

    // actions
    if (!opts.live) body.append(renderActions(msg, isUser, content));
    wrap.append(avatar, body);
    return wrap;
  }

  function renderSources(sources) {
    const box = el('div', { class: 'sources' });
    box.append(el('h4', { text: `Sources (${sources.length})` }));
    const grid = el('div', { class: 'source-grid' });
    sources.forEach((src) => {
      let host = '';
      try { host = new URL(src.url).hostname.replace(/^www\./, ''); } catch (e) { host = src.url; }
      grid.append(el('a', {
        class: 'source-card', id: 'source-' + src.index, href: src.url, target: '_blank', rel: 'noopener noreferrer',
      },
        el('span', { class: 'idx', text: String(src.index) }),
        src.title || host,
        el('span', { class: 'host', text: host }),
      ));
    });
    box.append(grid);
    return box;
  }

  function renderActions(msg, isUser, content) {
    const bar = el('div', { class: 'msg-actions' });
    if (!isUser) {
      const raw = msg.content || '';
      bar.append(el('button', {
        type: 'button', title: 'Copy',
        onclick: () => window.BatsMarkdown.copyText(raw).then(() => toast('Copied', 'ok', 1500)),
        html: '&#128203; Copy',
      }));
      bar.append(el('button', {
        type: 'button', title: 'Regenerate',
        onclick: () => regenerate(msg),
        html: '&#8635; Retry',
      }));
      bar.append(el('button', {
        type: 'button', title: 'Read aloud',
        onclick: (ev) => speak(raw, ev.currentTarget),
        html: '&#128266; Read',
      }));
      if (raw) {
        bar.append(el('button', {
          type: 'button', title: 'Edit and resend',
          onclick: () => {
            const input = $('#input');
            input.value = raw;
            autoGrow();
            input.focus();
          },
          html: '&#9998; Edit',
        }));
      }
    } else {
      bar.append(el('button', {
        type: 'button', title: 'Copy',
        onclick: () => window.BatsMarkdown.copyText(msg.content || '').then(() => toast('Copied', 'ok', 1500)),
        html: '&#128203; Copy',
      }));
      bar.append(el('button', {
        type: 'button', title: 'Edit message',
        onclick: async () => {
          const text = prompt('Edit your message', msg.content || '');
          if (text === null || text === msg.content) return;
          await api(`/api/conversations/${state.conversationId}/messages/${msg.id}/delete-after`, { method: 'POST' });
          $('#input').value = text;
          autoGrow();
          $('#input').focus();
          await openConversation(state.conversationId, true);
        },
        html: '&#9998; Edit',
      }));
    }
    return bar;
  }

  let speaking = null;
  function speak(text, btn) {
    if (!('speechSynthesis' in window)) { toast('Speech is not supported in this browser', 'error'); return; }
    if (speaking) {
      window.speechSynthesis.cancel();
      if (speaking === btn) { speaking = null; btn.classList.remove('active'); return; }
      speaking.classList.remove('active');
    }
    const clean = text.replace(/```[\s\S]*?```/g, ' code block ').replace(/[#*_>`]/g, '').slice(0, 4000);
    const utter = new SpeechSynthesisUtterance(clean);
    utter.onend = () => { speaking = null; btn.classList.remove('active'); };
    speaking = btn;
    btn.classList.add('active');
    window.speechSynthesis.speak(utter);
  }

  /* --------------------------------------------------------- conversation */
  async function loadMessages() {
    const inner = clearThread();
    state.messages.forEach((msg) => inner.append(renderMessage(msg)));
    showEmpty(false);
    scrollToBottom(false);
  }

  async function openConversation(id, silent = false) {
    if (state.streaming) { toast('Wait for the current answer (or press stop)', 'error'); return; }
    try {
      const data = await api(`/api/conversations/${id}`);
      LS.set('lastChat', id);
      state.conversationId = id;
      state.messages = (data.messages || []).map((m) => ({
        ...m,
        sources: m.sources || m.meta?.sources || [],
      }));
      const conv = data.conversation || {};
      if (conv.provider && providerEntry(conv.provider)) state.provider = conv.provider;
      if (conv.model) state.model = conv.model;
      renderModelButton();
      renderStatus();
      await loadMessages();
      renderChatList();
      closeSidebar();
      if (!silent) $('#input').focus();
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  function newChat() {
    if (state.streaming) stopStreaming();
    state.conversationId = null;
    state.messages = [];
    clearThread();
    showEmpty(true);
    renderChatList();
    closeSidebar();
    $('#input').value = '';
    autoGrow();
    clearAttachments();
    setImageMode(false);
    $('#input').focus();
  }

  /* --------------------------------------------------------------- upload */
  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []).filter(Boolean);
    if (!files.length) return;
    const limit = state.config?.limits?.max_attachments || 6;
    if (state.pendingAttachments.length + files.length > limit) {
      toast(`You can attach up to ${limit} images`, 'error');
      return;
    }
    for (const file of files) {
      const temp = { id: 'tmp-' + uid(), name: file.name, localUrl: URL.createObjectURL(file), uploading: true };
      state.pendingAttachments.push(temp);
      state.uploading++;
      renderAttachments();
      const form = new FormData();
      form.append('files', file, file.name);
      try {
        const res = await fetch('/api/upload', {
          method: 'POST',
          body: form,
          headers: state.accessCode ? { 'X-Bats-Code': state.accessCode } : {},
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || 'Upload failed');
        }
        const data = await res.json();
        const stored = (data.items || [])[0];
        const idx = state.pendingAttachments.indexOf(temp);
        if (idx >= 0 && stored) {
          state.pendingAttachments[idx] = { ...stored, localUrl: temp.localUrl };
        } else if (stored) {
          state.pendingAttachments.push(stored);
        }
      } catch (e) {
        const idx = state.pendingAttachments.indexOf(temp);
        if (idx >= 0) state.pendingAttachments.splice(idx, 1);
        toast(`${file.name}: ${e.message}`, 'error', 6000);
      } finally {
        state.uploading--;
        renderAttachments();
      }
    }
  }

  function renderAttachments() {
    const tray = $('#attachmentTray');
    tray.innerHTML = '';
    const ready = state.pendingAttachments.filter((a) => !a.uploading);
    if (!state.pendingAttachments.length) { tray.hidden = true; return; }
    tray.hidden = false;
    state.pendingAttachments.forEach((att) => {
      const chip = el('div', { class: 'att-chip' + (att.uploading ? ' uploading' : '') });
      if (att.uploading) {
        chip.append(el('div', { class: 'spinner' }));
      } else {
        chip.append(el('img', { src: att.preview_url || att.localUrl, alt: att.name || '' }));
        chip.append(el('button', {
          class: 'rm', type: 'button', title: 'Remove', text: '×',
          onclick: () => {
            state.pendingAttachments = state.pendingAttachments.filter((a) => a !== att);
            renderAttachments();
          },
        }));
      }
      tray.append(chip);
    });
    if (ready.length && !state.pendingAttachments.some((a) => a.uploading)) {
      tray.append(el('span', { class: 'small', style: 'align-self:center;color:var(--text-faint);font-size:11.5px',
        text: `${ready.length} image${ready.length > 1 ? 's' : ''} ready` }));
    }
  }

  function clearAttachments() {
    state.pendingAttachments.forEach((a) => { if (a.localUrl) URL.revokeObjectURL(a.localUrl); });
    state.pendingAttachments = [];
    renderAttachments();
  }

  /* ------------------------------------------------------------- composer */
  function autoGrow() {
    const input = $('#input');
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 220) + 'px';
  }

  function setStreaming(on) {
    state.streaming = on;
    const btn = $('#sendBtn');
    btn.classList.toggle('streaming', on);
    btn.title = on ? 'Stop generating' : 'Send message';
    renderStatus();
  }

  function stopStreaming() {
    if (!state.controller) return;
    try { state.controller.abort(); } catch (e) {}
    fetch('/api/chat/stop', { method: 'POST', headers: { 'X-Client-Id': state.clientId } }).catch(() => {});
    setStreaming(false);
  }

  async function send() {
    const input = $('#input');
    const text = input.value.trim();
    if (state.streaming) { stopStreaming(); return; }
    if (state.imageMode) { return generateImage(); }
    if (!text && !state.pendingAttachments.length) return;
    if (state.pendingAttachments.some((a) => a.uploading)) {
      toast('Wait for the uploads to finish', 'error');
      return;
    }

    const attachments = state.pendingAttachments.map((a) => a.id).filter(Boolean);
    const localPreviews = state.pendingAttachments.map((a) => ({
      id: a.id, name: a.name, url: a.localUrl || a.url, preview_url: a.localUrl || a.preview_url,
    }));

    input.value = '';
    autoGrow();
    clearAttachments();
    showEmpty(false);

    const userMsg = {
      id: 'local-' + uid(),
      role: 'user',
      content: text,
      attachments: localPreviews,
      created_at: Date.now() / 1000,
    };
    state.messages.push(userMsg);
    const inner = threadInner();
    inner.append(renderMessage(userMsg, { live: true }));
    scrollToBottom();

    await runStream({
      provider: state.provider,
      model: state.model,
      conversation_id: state.conversationId || '',
      message: text,
      attachments,
      web_search: state.searchOn,
      temperature: state.prefs.temperature,
      max_tokens: state.prefs.maxTokens,
      api_key: currentKey(state.provider),
      base_url: currentBaseUrl(state.provider),
      client_id: state.clientId,
    }, inner);
  }

  async function regenerate(msg) {
    if (state.streaming || !state.conversationId) return;
    const index = state.messages.findIndex((m) => m.id === msg.id);
    const lastUser = [...state.messages.slice(0, index < 0 ? undefined : index)].reverse()
      .find((m) => m.role === 'user');
    if (!lastUser) return;
    state.messages = state.messages.slice(0, Math.max(0, state.messages.indexOf(lastUser) + 1));
    await loadMessages();
    const inner = threadInner();
    await runStream({
      provider: state.provider,
      model: state.model,
      conversation_id: state.conversationId,
      message: lastUser.content,
      attachments: (lastUser.attachments || []).map((a) => a.id),
      web_search: state.searchOn,
      regenerate: true,
      temperature: state.prefs.temperature,
      max_tokens: state.prefs.maxTokens,
      api_key: currentKey(state.provider),
      base_url: currentBaseUrl(state.provider),
      client_id: state.clientId,
    }, inner);
  }

  async function runStream(payload, inner) {
    // placeholder assistant message
    const assistantEl = el('div', { class: 'msg assistant' });
    const avatar = el('div', { class: 'avatar', text: 'B' });
    const body = el('div', { class: 'msg-body' });
    const nameRow = el('div', { class: 'msg-name' }, 'Bats AI', el('span', { class: 'meta', text: payload.model }));
    const statusRow = el('div', { class: 'small', style: 'color:var(--text-faint);font-size:12.5px;margin:2px 0 6px' });
    statusRow.append(el('span', { class: 'typing' }, el('i'), el('i'), el('i')));
    const md = el('div', { class: 'md' });
    body.append(nameRow, statusRow, md);
    assistantEl.append(avatar, body);
    inner.append(assistantEl);
    scrollToBottom();

    state.controller = new AbortController();
    setStreaming(true);

    let answer = '';
    let pendingFrame = null;
    const renderAnswer = () => {
      pendingFrame = null;
      const stick = nearBottom();
      md.innerHTML = window.BatsMarkdown.render(answer) + '<span class="caret"></span>';
      window.BatsMarkdown.enhance(md);
      if (stick) scrollToBottom(false);
    };
    const scheduleRender = () => {
      if (pendingFrame) return;
      pendingFrame = requestAnimationFrame(renderAnswer);
    };
    let finishedOk = false;

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify(payload),
        signal: state.controller.signal,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Request failed (${res.status})`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let sources = [];

      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop();
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith('data:')) continue;
          const data = line.slice(5).trim();
          if (data === '[DONE]') continue;
          let evt;
          try { evt = JSON.parse(data); } catch (e) { continue; }

          if (evt.type === 'start') {
            state.conversationId = evt.conversation_id;
            if (evt.key_source === 'client') nameRow.append(el('span', { class: 'meta', text: 'your key' }));
            if (evt.vision === false && payload.attachments?.length) {
              statusRow.textContent = 'This model cannot read images — switching to a vision model helps.';
            }
          } else if (evt.type === 'status') {
            statusRow.textContent = evt.text;
          } else if (evt.type === 'sources') {
            sources = evt.items || [];
            statusRow.textContent = `${sources.length} sources · ${evt.provider}`;
          } else if (evt.type === 'text') {
            if (!answer) statusRow.textContent = '';
            answer += evt.text;
            scheduleRender();
          } else if (evt.type === 'thinking') {
            statusRow.textContent = 'Reasoning…';
          } else if (evt.type === 'image') {
            const grid = el('div', { class: 'msg-images' });
            const img = el('img', { src: evt.image.url, alt: evt.image.prompt, loading: 'lazy' });
            img.addEventListener('click', () => openLightbox(evt.image.url, evt.image.prompt, evt.image.prompt));
            grid.append(el('figure', {}, img,
              el('div', { class: 'img-actions' },
                el('a', { href: evt.image.url, download: `bats-${evt.image.id}.png`, text: 'Download' }),
                el('button', { type: 'button', text: 'Copy prompt',
                  onclick: () => window.BatsMarkdown.copyText(evt.image.prompt).then(() => toast('Prompt copied', 'ok', 1500)) }),
              )));
            body.insertBefore(grid, md);
            scrollToBottom();
          } else if (evt.type === 'done') {
            finishedOk = true;
            if (pendingFrame) { cancelAnimationFrame(pendingFrame); pendingFrame = null; }
            md.innerHTML = window.BatsMarkdown.render(answer);
            window.BatsMarkdown.enhance(md);
            if (sources.length) body.append(renderSources(sources));
            statusRow.remove();
            const metaBits = [evt.model || payload.model, evt.elapsed ? `${evt.elapsed}s` : '',
              evt.usage?.total_tokens ? `${evt.usage.total_tokens} tokens` : ''].filter(Boolean).join(' · ');
            if (metaBits) nameRow.append(el('span', { class: 'meta', text: metaBits }));
            const msgObj = {
              id: evt.message_id, role: 'assistant', content: answer, sources,
              meta: { model: evt.model || payload.model, elapsed: evt.elapsed, usage: evt.usage },
            };
            body.append(renderActions(msgObj, false, md));
            state.conversationId = evt.conversation_id;
            state.messages.push(msgObj);
            await refreshConversations();
            if (evt.stopped) toast('Stopped', '', 1500);
          } else if (evt.type === 'error') {
            statusRow.remove();
            md.innerHTML = '';
            md.append(el('div', { style: 'color:var(--danger);font-size:14px', text: evt.message || 'Something went wrong' }));
            md.append(el('div', { style: 'margin-top:8px;display:flex;gap:8px;flex-wrap:wrap' },
              el('button', {
                class: 'ghost-btn', type: 'button', style: 'padding:5px 10px;font-size:12px',
                text: 'Retry',
                onclick: () => regenerate({ id: 'retry-' + uid() }),
              }),
              el('button', {
                class: 'ghost-btn', type: 'button', style: 'padding:5px 10px;font-size:12px',
                text: 'Optional: use my own key',
                onclick: () => openSettings('keys'),
              }),
            ));
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        statusRow.remove();
        md.innerHTML = '';
        md.append(el('div', { style: 'color:var(--danger)', text: err.message || 'Network error' }));
      } else if (answer) {
        statusRow.remove();
      }
    } finally {
      setStreaming(false);
      state.controller = null;
      renderStatus();
      if (finishedOk || state.conversationId) await refreshConversations();
    }
  }

  /* --------------------------------------------------------- image mode */
  function setImageMode(on) {
    state.imageMode = on;
    renderImagePanel();
    renderStatus();
  }

  function renderImagePanel() {
    let panel = $('#imagePanel');
    if (!state.imageMode) { if (panel) panel.hidden = true; return; }
    if (!panel) {
      panel = el('div', { class: 'image-panel', id: 'imagePanel' });
      panel.append(
        el('div', { class: 'ip-head' },
          el('span', { html: '&#127912; Create an image' }),
          el('button', {
            type: 'button', title: 'Close', text: '×',
            onclick: () => setImageMode(false),
          }),
        ),
        el('textarea', {
          id: 'imagePromptInput', placeholder: 'Describe the image… e.g. “a neon bat logo on dark background, vector style”',
          oninput: (ev) => { state.imagePrompt = ev.target.value; },
        }),
        el('div', { class: 'ip-row' },
          el('label', {}, 'Size ',
            (() => {
              const sel = el('select', {
                onchange: (ev) => { state.prefs.imageSize = ev.target.value; LS.set('prefs', state.prefs); },
              });
              [['1024x1024', 'Square'], ['1024x1536', 'Portrait'], ['1536x1024', 'Landscape']].forEach(([v, label]) => {
                const opt = el('option', { value: v, text: label });
                if (state.prefs.imageSize === v) opt.selected = true;
                sel.append(opt);
              });
              return sel;
            })(),
          ),
          el('button', {
            type: 'button', class: 'primary-btn', style: 'margin-left:auto;padding:7px 14px;font-size:13px',
            text: 'Generate',
            onclick: () => generateImage(),
          }),
        ),
      );
      $('.composer-wrap').prepend(panel);
    }
    panel.hidden = false;
    if (state.imagePrompt) $('#imagePromptInput').value = state.imagePrompt;
  }

  async function generateImage() {
    const prompt = ($('#imagePromptInput')?.value || state.imagePrompt || $('#input').value || '').trim();
    if (!prompt) { toast('Describe the image first', 'error'); return; }
    if (!state.config?.image_providers?.length) {
      toast('Image generation is not configured on this server', 'error', 5000);
      return;
    }
    showEmpty(false);
    setImageMode(false);
    $('#input').value = '';
    autoGrow();

    const inner = threadInner();
    const userEl = renderMessage({ role: 'user', content: `🎨 ${prompt}` }, { live: true });
    inner.append(userEl);
    const holder = el('div', { class: 'msg assistant' });
    const body = el('div', { class: 'msg-body' });
    body.append(el('div', { class: 'msg-name' }, 'Bats AI', el('span', { class: 'meta', text: 'image' })));
    const status = el('div', { class: 'small', style: 'color:var(--text-faint)', text: 'Painting…' });
    const grid = el('div', { class: 'msg-images' });
    body.append(status, grid);
    holder.append(el('div', { class: 'avatar', text: 'B' }), body);
    inner.append(holder);
    scrollToBottom();

    try {
      const data = await api('/api/images/generate', {
        method: 'POST',
        body: JSON.stringify({
          prompt,
          provider: state.config.default_image_provider || '',
          size: state.prefs.imageSize || '1024x1024',
          conversation_id: state.conversationId || '',
          api_key: state.keys['OPENAI_API_KEY'] || '',
        }),
      });
      status.remove();
      const img = el('img', { src: data.url, alt: data.prompt });
      img.addEventListener('click', () => openLightbox(data.url, data.prompt, data.prompt));
      grid.append(el('figure', {}, img,
        el('div', { class: 'img-actions' },
          el('a', { href: data.url, download: `bats-${data.id}.png`, text: 'Download' }),
          el('button', { type: 'button', text: 'Copy prompt', onclick: () => window.BatsMarkdown.copyText(data.prompt) }),
          el('button', { type: 'button', text: 'Another', onclick: () => { state.imagePrompt = prompt; setImageMode(true); } }),
        )));
      scrollToBottom();
      if (state.conversationId) { await openConversation(state.conversationId, true); }
      else { await refreshConversations(); }
    } catch (e) {
      status.remove();
      grid.append(el('div', { style: 'color:var(--danger);font-size:14px', text: e.message }));
    }
  }

  /* ------------------------------------------------------------ lightbox */
  function openLightbox(url, name, promptText) {
    state.lightboxPrompt = promptText || '';
    $('#lightboxImg').src = url;
    const link = $('#lightboxDownload');
    link.href = url;
    link.download = name || 'bats-image.png';
    $('#lightbox').hidden = false;
  }
  function closeLightbox() { $('#lightbox').hidden = true; $('#lightboxImg').src = ''; }

  /* -------------------------------------------------------------- settings */
  function openSettings(tab = 'keys') {
    renderSettings();
    $('#settingsModal').hidden = false;
    switchTab(tab);
    $('#closeSettings').focus();
  }
  function closeSettings() { $('#settingsModal').hidden = true; }

  function switchTab(tab) {
    state.settingsTab = tab;
    $$('#settingsTabs .tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    $$('.tab-panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === tab));
  }

  function renderSettings() {
    // API keys
    const keyList = $('#keyList');
    keyList.innerHTML = '';
    const providers = state.config?.providers || [];
    // group env vars
    const seen = new Set();
    providers.forEach((provider) => {
      if (!provider.env_var || seen.has(provider.env_var)) return;
      seen.add(provider.env_var);
      const stored = state.keys[provider.env_var] || '';
      const row = el('div', { class: 'key-row' });
      row.append(el('div', { class: 'kl' },
        provider.label,
        el('small', { text: provider.env_var }),
      ));
      const input = el('input', {
        type: 'password', placeholder: provider.has_key ? 'using server key ✓' : 'paste key…',
        value: stored, autocomplete: 'off', spellcheck: 'false',
        oninput: (ev) => {
          const value = ev.target.value.trim();
          if (value) state.keys[provider.env_var] = value;
          else delete state.keys[provider.env_var];
          LS.set('keys', state.keys);
          statusEl.textContent = value ? 'saved in this browser' : (provider.has_key ? 'server key' : '');
          statusEl.className = 'key-status' + (value || provider.has_key ? ' on' : '');
          pickDefaultModel();
          renderModelButton();
          renderStatus();
        },
      });
      const statusEl = el('span', {
        class: 'key-status' + (provider.has_key ? ' on' : ''),
        text: provider.has_key ? 'server key' : (stored ? 'saved in this browser' : ''),
      });
      row.append(input, statusEl);
      keyList.append(row);
    });
    $('#customBaseUrl').value = state.keys['CUSTOM_BASE_URL'] || '';
    $('#customBaseUrl').oninput = (ev) => {
      const v = ev.target.value.trim();
      if (v) state.keys['CUSTOM_BASE_URL'] = v; else delete state.keys['CUSTOM_BASE_URL'];
      LS.set('keys', state.keys);
    };

    // model prefs
    $('#tempRange').value = state.prefs.temperature;
    $('#tempVal').textContent = Number(state.prefs.temperature).toFixed(1);
    $('#maxTokRange').value = state.prefs.maxTokens;
    $('#maxTokVal').textContent = state.prefs.maxTokens;
    $('#imageSize').value = state.prefs.imageSize || '1024x1024';

    const imageSelect = $('#imageProvider');
    imageSelect.innerHTML = '';
    (state.config?.image_providers || []).forEach((provider) => {
      const opt = el('option', { value: provider.id, text: provider.label + (provider.has_key ? '' : ' (key required)') });
      if (provider.id === state.config.default_image_provider) opt.selected = true;
      imageSelect.append(opt);
    });
    if (!(state.config?.image_providers || []).length) {
      imageSelect.append(el('option', { value: '', text: 'None configured' }));
    }

    // search
    const chips = $('#searchProviders');
    chips.innerHTML = '';
    (state.config?.search_providers || []).forEach((name) => {
      chips.append(el('span', { class: 'chip' + (name === 'duckduckgo' || name === 'wikipedia' ? '' : ' on'), text: name }));
    });
    $('#searchDefault').checked = state.searchOn;

    // access
    const accessInput = $('#accessCodeInput');
    accessInput.value = state.accessCode || '';
    accessInput.oninput = (ev) => {
      state.accessCode = ev.target.value.trim();
      LS.set('accessCode', state.accessCode);
    };
    const needs = state.config?.access_code_required;
    $('#accessStatus').textContent = needs
      ? (state.accessCode ? 'A code is required — stored in this browser.' : 'This deployment requires a code before chatting.')
      : 'This deployment is public (no access code set).';
    $('#shareLink').textContent = (location.origin + '/' + (state.accessCode ? '?code=' + encodeURIComponent(state.accessCode) : ''));

    // data
    const stats = $('#statsRow');
    stats.innerHTML = '';
    const convCount = state.conversations.length;
    stats.append(el('div', { class: 'stat' }, el('b', { text: convCount }), el('span', { text: 'Chats' })));
    const msgCount = state.conversations.reduce((sum, c) => sum + (c.message_count || 0), 0);
    stats.append(el('div', { class: 'stat' }, el('b', { text: msgCount }), el('span', { text: 'Messages' })));
    stats.append(el('div', { class: 'stat' }, el('b', { text: (state.config?.providers || []).filter((p) => p.configured).length }), el('span', { text: 'Server models' })));
    stats.append(el('div', { class: 'stat' }, el('b', { text: (state.config?.search_providers || []).length }), el('span', { text: 'Search engines' })));

    // about
    const about = $('#aboutList');
    about.innerHTML = '';
    const rows = [
      ['Chat providers', (state.config?.providers || []).filter((p) => p.configured).map((p) => p.label).join(', ') || 'none on server (paste a key)'],
      ['Image engines', (state.config?.image_providers || []).map((p) => p.label).join(', ') || 'none configured'],
      ['Web search', (state.config?.search_providers || []).join(' → ') || 'disabled'],
      ['Upload limit', `${state.config?.limits?.max_upload_mb || 20} MB · up to ${state.config?.limits?.max_attachments || 6} images`],
      ['Hosting', location.host || 'local'],
    ];
    rows.forEach(([label, value]) => {
      about.append(el('li', {}, el('b', { text: label + ':' }), ' ', value));
    });

    // listeners (bound once)
    if (!$('#tempRange').dataset.bound) {
      $('#tempRange').dataset.bound = '1';
      $('#tempRange').addEventListener('input', (ev) => {
        state.prefs.temperature = Number(ev.target.value);
        $('#tempVal').textContent = Number(ev.target.value).toFixed(1);
        LS.set('prefs', state.prefs);
      });
      $('#maxTokRange').addEventListener('input', (ev) => {
        state.prefs.maxTokens = Number(ev.target.value);
        $('#maxTokVal').textContent = ev.target.value;
        LS.set('prefs', state.prefs);
      });
      $('#imageSize').addEventListener('change', (ev) => {
        state.prefs.imageSize = ev.target.value; LS.set('prefs', state.prefs);
      });
      $('#searchDefault').addEventListener('change', (ev) => {
        state.searchOn = ev.target.checked; LS.set('searchOn', state.searchOn); renderStatus();
      });
      $('#settingsTabs').addEventListener('click', (ev) => {
        const tab = ev.target.closest('.tab');
        if (tab) switchTab(tab.dataset.tab);
      });
      $('#closeSettings').addEventListener('click', closeSettings);
      $('#saveSettings').addEventListener('click', () => { closeSettings(); toast('Settings saved', 'ok', 1600); });
      $('#settingsModal').addEventListener('click', (ev) => { if (ev.target.id === 'settingsModal') closeSettings(); });
      $('#resetSettings').addEventListener('click', () => {
        state.prefs = { temperature: 0.7, maxTokens: 2048, imageSize: '1024x1024' };
        LS.set('prefs', state.prefs);
        renderSettings();
        toast('Defaults restored');
      });
      $('#clearAllChats').addEventListener('click', async () => {
        if (!confirm('Delete every chat? This cannot be undone.')) return;
        await api('/api/conversations', { method: 'DELETE' });
        newChat();
        await refreshConversations();
        toast('All chats deleted', 'ok');
      });
      $('#clearLocal').addEventListener('click', () => {
        if (!confirm('Remove API keys and preferences stored in this browser?')) return;
        LS.del('keys'); LS.del('prefs'); LS.del('selection');
        state.keys = {}; state.prefs = { temperature: 0.7, maxTokens: 2048, imageSize: '1024x1024' };
        pickDefaultModel(); renderModelButton(); renderSettings(); renderStatus();
        toast('Browser data cleared', 'ok');
      });
      $('#imageProvider').addEventListener('change', () => toast('Image engine preference noted (server decides default)', '', 2200));
    }
  }

  /* --------------------------------------------------------------- sidebar */
  function toggleSidebar(force) {
    const app = $('#app');
    const open = force !== undefined ? force : !app.classList.contains('sidebar-open');
    app.classList.toggle('sidebar-open', open);
    $('#sidebarBackdrop').hidden = !open;
  }
  function closeSidebar() { toggleSidebar(false); }

  /* ------------------------------------------------------------- suggestions */
  const SUGGESTIONS = [
    { icon: '&#128269;', title: 'Search the web', sub: 'Latest AI news today', prompt: 'Search the web for the latest AI news today and summarise the top 5 stories with sources.' },
    { icon: '&#127912;', title: 'Create an image', sub: 'A neon bat logo', prompt: 'Generate an image of a neon bat logo on a dark background, vector style', image: true },
    { icon: '&#128200;', title: 'Analyse data', sub: 'Explain a chart I upload', prompt: 'I will upload an image of a chart. Explain the trend, the key numbers and what it means.' },
    { icon: '&#128187;', title: 'Write code', sub: 'A React hook with tests', prompt: 'Write a React hook that debounces a value, with TypeScript types and a small test.' },
  ];

  function renderSuggestions() {
    const box = $('#suggestions');
    box.innerHTML = '';
    SUGGESTIONS.forEach((s) => {
      box.append(el('button', {
        class: 'suggestion', type: 'button',
        onclick: () => {
          if (s.image) { setImageMode(true); state.imagePrompt = s.prompt; renderImagePanel(); $('#imagePromptInput').focus(); return; }
          $('#input').value = s.prompt;
          autoGrow();
          $('#input').focus();
          if (s.prompt.includes('Search')) { state.searchOn = true; LS.set('searchOn', true); renderStatus(); }
        },
      },
        el('span', { class: 's-ico', html: s.icon }),
        el('span', {}, el('b', { text: s.title }), el('span', { text: s.sub })),
      ));
    });
  }

  /* -------------------------------------------------------------- boot/init */
  function bindEvents() {
    const input = $('#input');
    input.addEventListener('input', autoGrow);
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
        ev.preventDefault();
        send();
      }
    });
    input.addEventListener('paste', (ev) => {
      const files = Array.from(ev.clipboardData?.files || []).filter((f) => f.type.startsWith('image/'));
      if (files.length) { ev.preventDefault(); uploadFiles(files); }
    });
    $('#composer').addEventListener('submit', (ev) => { ev.preventDefault(); send(); });
    $('#sendBtn').addEventListener('click', (ev) => { ev.preventDefault(); send(); });
    $('#attachBtn').addEventListener('click', () => $('#fileInput').click());
    $('#fileInput').addEventListener('change', (ev) => { uploadFiles(ev.target.files); ev.target.value = ''; });
    $('#searchBtn').addEventListener('click', () => {
      state.searchOn = !state.searchOn;
      LS.set('searchOn', state.searchOn);
      renderStatus();
      toast(state.searchOn ? 'Web search on' : 'Web search off', '', 1500);
    });
    $('#imageBtn').addEventListener('click', () => setImageMode(!state.imageMode));
    $('#newChatBtn').addEventListener('click', newChat);
    $('#newChatTop').addEventListener('click', newChat);
    $('#settingsBtn').addEventListener('click', () => openSettings('keys'));
    $('#menuBtn').addEventListener('click', () => toggleSidebar());
    $('#sidebarBackdrop').addEventListener('click', closeSidebar);
    $('#themeBtn').addEventListener('click', toggleTheme);
    $('#shareBtn').addEventListener('click', exportChat);

    $('#modelBtn').addEventListener('click', (ev) => {
      ev.stopPropagation();
      const menu = $('#modelMenu');
      const open = menu.hidden;
      closeMenus();
      if (open) {
        renderModelMenu();
        menu.hidden = false;
        $('#modelBtn').setAttribute('aria-expanded', 'true');
      }
    });
    document.addEventListener('click', (ev) => {
      if (!ev.target.closest('.model-picker')) closeMenus();
    });

    $('#historySearch').addEventListener('input', (ev) => {
      const q = ev.target.value.trim().toLowerCase();
      $$('#chatList .chat-item').forEach((item) => {
        const conv = state.conversations.find((c) => c.id === item.dataset.id);
        const hay = ((conv?.title || '') + ' ' + (conv?.preview || '')).toLowerCase();
        item.style.display = hay.includes(q) ? '' : 'none';
      });
    });

    $('#lightboxClose').addEventListener('click', closeLightbox);
    $('#lightbox').addEventListener('click', (ev) => { if (ev.target.id === 'lightbox') closeLightbox(); });
    $('#lightboxCopy').addEventListener('click', () => {
      if (!state.lightboxPrompt) return toast('No prompt to copy');
      window.BatsMarkdown.copyText(state.lightboxPrompt).then(() => toast('Prompt copied', 'ok', 1500));
    });

    $('#thread').addEventListener('scroll', () => {
      const top = $('#thread').scrollTop;
      $('.topbar').classList.toggle('scrolled', top > 6);
    });

    document.addEventListener('keydown', (ev) => {
      const meta = ev.ctrlKey || ev.metaKey;
      if (meta && ev.shiftKey && ev.key.toLowerCase() === 'o') { ev.preventDefault(); newChat(); }
      if (meta && ev.key === '/') { ev.preventDefault(); $('#settingsModal').hidden ? openSettings('keys') : closeSettings(); }
      if (ev.key === 'Escape') {
        if (!$('#lightbox').hidden) closeLightbox();
        else if (!$('#settingsModal').hidden) closeSettings();
        else if (!$('#modelMenu').hidden) closeMenus();
        else closeSidebar();
      }
    });

    window.addEventListener('beforeunload', (ev) => {
      if (state.streaming) { ev.preventDefault(); ev.returnValue = ''; }
    });

    // drag & drop images anywhere
    ['dragover', 'drop'].forEach((type) => {
      document.addEventListener(type, (ev) => {
        ev.preventDefault();
        if (type === 'drop') {
          const files = Array.from(ev.dataTransfer?.files || []).filter((f) => f.type.startsWith('image/') || /\.(heic|heif|avif|tiff?|svg)$/i.test(f.name));
          if (files.length) uploadFiles(files);
        }
      });
    });
  }

  function toggleTheme() {
    const root = document.documentElement;
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    LS.set('theme', next);
    document.querySelector('meta[name="theme-color"]').setAttribute('content', next === 'dark' ? '#0b0d12' : '#ffffff');
  }

  function exportChat() {
    if (!state.messages.length) { toast('Nothing to export yet'); return; }
    const lines = [`# ${state.conversations.find((c) => c.id === state.conversationId)?.title || 'Bats AI chat'}`, ''];
    state.messages.forEach((m) => {
      lines.push(`## ${m.role === 'user' ? 'You' : 'Bats AI'}`, '', m.content || '', '');
      (m.sources || []).forEach((s) => lines.push(`- [${s.index}] ${s.title} — ${s.url}`));
      if ((m.sources || []).length) lines.push('');
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
    const a = el('a', { href: URL.createObjectURL(blob), download: 'bats-chat.md' });
    document.body.append(a); a.click(); a.remove();
    toast('Exported markdown', 'ok');
  }

  let booted = false;
  async function init() {
    if (booted) return;      // DOMContentLoaded can fire more than once
    booted = true;
    try {
      const code = new URLSearchParams(location.search).get('code');
      if (code) { state.accessCode = code; LS.set('accessCode', code); }
    } catch (e) { /* ignore */ }
    bindEvents();
    renderSuggestions();

    // theme meta
    const theme = document.documentElement.dataset.theme || 'dark';
    document.querySelector('meta[name="theme-color"]').setAttribute('content', theme === 'dark' ? '#0b0d12' : '#ffffff');

    try {
      state.config = await api('/api/config');
    } catch (e) {
      toast('Could not reach the server: ' + e.message, 'error', 8000);
      state.config = { providers: [], image_providers: [], search_providers: [], limits: {}, features: {} };
    }
    pickDefaultModel();
    renderModelButton();
    renderStatus();
    renderChatList();
    renderAttachments();
    await refreshConversations();

    // restore the last open chat
    const lastId = LS.get('lastChat', null);
    if (lastId && state.conversations.some((c) => c.id === lastId)) {
      openConversation(lastId, true);
    }
    if (!usableProviders().length) {
      // last resort only: free mode normally always yields a provider
      setTimeout(() => {
        if (!usableProviders().length) {
          openSettings('keys');
          toast('No model available yet — check FREE_MODE or add a key', '', 6000);
        }
      }, 1500);
    }
    autoGrow();
    $('#input').focus();
  }

  document.addEventListener('DOMContentLoaded', () => {
    // speed up: fetch conversation records before full init when possible
    init();
  });
})();
