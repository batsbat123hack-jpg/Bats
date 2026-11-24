/* ==========================================================================
   Bats AI — tiny, safe Markdown renderer (no dependencies)
   Supports: headings, emphasis, lists (nested), task lists, blockquotes,
   fenced code with highlighting + copy, inline code, links, images, tables,
   rules, LaTeX passthrough ($...$, $$...$$) and [n] web citations.
   All HTML is escaped first; only an allowlist of tags is emitted.
   ========================================================================== */
(function (global) {
  'use strict';

  const ESCAPE_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESCAPE_MAP[c]);

  const KEYWORDS = {
    js: 'const let var function return if else for while class extends new async await import from export default try catch throw typeof instanceof of in null undefined true false this super yield delete void switch case break continue do static get set',
    ts: 'const let var function return if else for while class extends new async await import from export default try catch throw typeof instanceof of in null undefined true false this super yield delete void switch case break continue do static get set interface type implements enum public private protected readonly namespace declare as',
    python: 'def class return if elif else for while import from as with try except finally raise yield lambda pass break continue global nonlocal assert del in is not and or None True False async await self',
    bash: 'if then else elif fi for while do done case esac function return echo export local readonly cd source alias sudo grep sed awk cat printf read set unset exit',
    json: 'true false null',
    sql: 'select from where insert into values update set delete create table alter drop join left right inner outer on group by order having limit offset as distinct union all and or not null is like in exists case when then else end',
    css: '',
    html: '',
  };

  function highlight(code, lang) {
    const l = (lang || '').toLowerCase();
    const key = ['js', 'javascript', 'jsx', 'mjs', 'ts', 'typescript', 'tsx'].includes(l) ? 'js'
      : ['py', 'python'].includes(l) ? 'python'
      : ['sh', 'bash', 'shell', 'zsh', 'console'].includes(l) ? 'bash'
      : ['yml', 'yaml', 'toml', 'ini'].includes(l) ? 'bash'
      : ['json'].includes(l) ? 'json'
      : ['sql'].includes(l) ? 'sql'
      : null;
    let out = esc(code);
    if (!key) return out;

    // Protect strings/comments in placeholders that contain no digits, so the
    // number/keyword passes below cannot touch their contents.
    const tokens = [];
    const PH = '\u0000';
    const stash = (html) => PH + String.fromCharCode(0xE000 + tokens.push(html) - 1) + PH;
    const unstash = (text) =>
      text.replace(/\u0000([\uE000-\uF8FF])\u0000/g, (m, ch) => tokens[ch.charCodeAt(0) - 0xE000]);

    if (key === 'bash' || key === 'python') {
      out = out.replace(/(^|\n)(\s*)#([^\n]*)/g, (m, nl, ws, rest) =>
        nl + ws + stash(`<span class="tk-com">#${rest}</span>`));
    }
    if (key === 'js' || key === 'sql') {
      out = out.replace(/(^|\n)(\s*)\/\/([^\n]*)/g, (m, nl, ws, rest) =>
        nl + ws + stash(`<span class="tk-com">//${rest}</span>`));
      out = out.replace(/(?<!:)\/\/(?![^\n]*["'`])[^\n]*/g, (m) => stash(`<span class="tk-com">${m}</span>`));
    }
    out = out.replace(/(&#39;|&quot;|`)(?:\\.|(?!\1)[^\\\n])*\1/g, (m) => stash(`<span class="tk-str">${m}</span>`));
    out = out.replace(/\b(\d+(?:\.\d+)?)\b/g, (m) => stash(`<span class="tk-num">${m}</span>`));

    const kw = KEYWORDS[key];
    if (kw) {
      const re = new RegExp(`\\b(${kw.trim().split(/\s+/).join('|')})\\b`, 'g');
      out = out.replace(re, (m) => stash(`<span class="tk-kw">${m}</span>`));
    }
    if (key === 'bash') {
      out = out.replace(/\b([A-Z_][A-Z0-9_]{2,})\b/g, (m) => stash(`<span class="tk-kw">${m}</span>`));
    }
    return unstash(out);
  }

  function inline(text) {
    let out = esc(text);

    // inline math before other processing (keep $..$ literal)
    const math = [];
    out = out.replace(/\$([^$\n]+?)\$/g, (m, expr) => `\u0001${math.push(expr) - 1}\u0001`);

    // citations [1] or [1,2] or [1][2]
    out = out.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (m, nums) => {
      return nums.split(',').map((n) => `<a class="cite" href="#source-${n.trim()}" data-cite="${n.trim()}">${n.trim()}</a>`).join('');
    });

    // inline code
    const code = [];
    out = out.replace(/`([^`\n]+)`/g, (m, c) => `\u0002${code.push(`<code>${c}</code>`) - 1}\u0002`);

    // images then links
    out = out.replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g, (m, alt, src) => `<img src="${src}" alt="${alt}" loading="lazy" />`);
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+|\/[^\s)]*)\)/g,
      (m, label, href) => `<a href="${href}" target="_blank" rel="noopener noreferrer nofollow">${label}</a>`);
    // bare urls
    out = out.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g,
      (m, pre, url) => `${pre}<a href="${url}" target="_blank" rel="noopener noreferrer nofollow">${url}</a>`);

    out = out.replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>');
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    out = out.replace(/(^|[\s(])_([^_\n]+)_/g, '$1<em>$2</em>');
    out = out.replace(/~~([^~]+)~~/g, '<del>$1</del>');
    out = out.replace(/==([^=]+)==/g, '<mark>$1</mark>');

    out = out.replace(/\u0002(\d+)\u0002/g, (m, i) => code[Number(i)]);
    out = out.replace(/\u0001(\d+)\u0001/g, (m, i) => `<span class="katex-math">${math[Number(i)]}</span>`);
    return out;
  }

  function renderTable(lines) {
    const rows = lines.map((l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
    if (rows.length < 2) return null;
    const aligns = rows[1].map((c) => {
      const l = c.startsWith(':'), r = c.endsWith(':');
      return l && r ? 'center' : r ? 'right' : l ? 'left' : '';
    });
    const head = rows[0];
    const body = rows.slice(2);
    let html = '<table><thead><tr>';
    head.forEach((c, i) => { html += `<th${aligns[i] ? ` style="text-align:${aligns[i]}"` : ''}>${inline(c)}</th>`; });
    html += '</tr></thead><tbody>';
    body.forEach((r) => {
      html += '<tr>';
      head.forEach((_, i) => { html += `<td${aligns[i] ? ` style="text-align:${aligns[i]}"` : ''}>${inline(r[i] ?? '')}</td>`; });
      html += '</tr>';
    });
    return html + '</tbody></table>';
  }

  let uid = 0;

  function render(src) {
    if (!src) return '';
    const lines = String(src).replace(/\r\n?/g, '\n').split('\n');
    const html = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      // fenced code
      const fence = line.match(/^\s*(```+|~~~+)\s*([\w+-]*)\s*$/);
      if (fence) {
        const marker = fence[1][0].repeat(3);
        const lang = fence[2] || '';
        const buf = [];
        i++;
        while (i < lines.length && !new RegExp(`^\\s*${marker[0]}{3,}\\s*$`).test(lines[i])) {
          buf.push(lines[i]); i++;
        }
        i++;
        const code = buf.join('\n');
        const id = 'code-' + (++uid);
        html.push(
          `<div class="code-block"><div class="code-head"><span>${esc(lang || 'text')}</span>` +
          `<button class="copy-code" data-copy="${id}" type="button">Copy</button></div>` +
          `<pre><code id="${id}">${highlight(code, lang)}</code></pre></div>`
        );
        continue;
      }

      // display math
      if (/^\s*\$\$\s*$/.test(line) || /^\s*\$\$.*\$\$\s*$/.test(line)) {
        if (/^\s*\$\$\s*$/.test(line)) {
          const buf = [];
          i++;
          while (i < lines.length && !/^\s*\$\$\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
          i++;
          html.push(`<div class="math-block">${esc(buf.join('\n'))}</div>`);
        } else {
          html.push(`<div class="math-block">${esc(line.replace(/^\s*\$\$|\$\$\s*$/g, ''))}</div>`);
          i++;
        }
        continue;
      }

      // horizontal rule
      if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(line)) { html.push('<hr />'); i++; continue; }

      // heading
      const h = line.match(/^\s{0,3}(#{1,6})\s+(.*)$/);
      if (h) {
        const level = h[1].length;
        html.push(`<h${level}>${inline(h[2].replace(/\s+#+\s*$/, ''))}</h${level}>`);
        i++;
        continue;
      }

      // table
      if (/\|/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:*-]+\|[\s:|-]*$/.test(lines[i + 1])) {
        const buf = [];
        while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim()) { buf.push(lines[i]); i++; }
        const table = renderTable(buf);
        if (table) { html.push(table); continue; }
        i -= buf.length;
      }

      // blockquote
      if (/^\s*>\s?/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          buf.push(lines[i].replace(/^\s*>\s?/, '')); i++;
        }
        html.push(`<blockquote>${render(buf.join('\n'))}</blockquote>`);
        continue;
      }

      // lists
      const listMatch = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
      if (listMatch) {
        const ordered = /\d/.test(listMatch[2]);
        const baseIndent = listMatch[1].length;
        const items = [];
        while (i < lines.length) {
          const m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
          if (!m || m[1].length < baseIndent) break;
          if (m[1].length > baseIndent) {
            // nested block belongs to previous item
            const nested = [];
            while (i < lines.length) {
              const n = lines[i];
              if (!n.trim()) { i++; continue; }
              const nm = n.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
              if (!nm || nm[1].length <= baseIndent) break;
              nested.push(n.slice(baseIndent + 2)); i++;
            }
            if (items.length) items[items.length - 1].extra = nested.join('\n');
            continue;
          }
          items.push({ text: m[3], extra: '' });
          i++;
          // lazy continuation lines
          while (i < lines.length && lines[i].trim() && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) && !/^\s*(```|#{1,6}\s|>)/.test(lines[i])) {
            items[items.length - 1].text += '\n' + lines[i].trim();
            i++;
          }
        }
        const tag = ordered ? 'ol' : 'ul';
        let body = '';
        items.forEach((item) => {
          const task = item.text.match(/^\[( |x|X)\]\s+(.*)$/);
          if (task) {
            const checked = task[1].toLowerCase() === 'x' ? ' checked' : '';
            body += `<li class="task"><input type="checkbox" disabled${checked} /> ${inline(task[2])}${item.extra ? render(item.extra) : ''}</li>`;
          } else {
            body += `<li>${inline(item.text)}${item.extra ? render(item.extra) : ''}</li>`;
          }
        });
        html.push(`<${tag}>${body}</${tag}>`);
        continue;
      }

      // blank
      if (!line.trim()) { i++; continue; }

      // paragraph
      const buf = [];
      while (
        i < lines.length && lines[i].trim() &&
        !/^\s*(```|~~~|#{1,6}\s|>|\s*([-*+]|\d+[.)])\s+)/.test(lines[i]) &&
        !/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(lines[i])
      ) { buf.push(lines[i]); i++; }
      if (!buf.length) { i++; continue; }
      html.push(`<p>${inline(buf.join('\n')).replace(/\n/g, '<br />')}</p>`);
    }
    return html.join('\n');
  }

  /* ---------------------------------------------------------------- extras */
  function enhance(root) {
    root.querySelectorAll('.copy-code').forEach((btn) => {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', () => {
        const target = root.querySelector('#' + btn.dataset.copy);
        const text = target ? target.innerText : '';
        copyText(text).then(() => {
          btn.textContent = 'Copied!';
          setTimeout(() => { btn.textContent = 'Copy'; }, 1400);
        });
      });
    });
    root.querySelectorAll('a.cite').forEach((a) => {
      a.addEventListener('click', (ev) => {
        const card = document.getElementById('source-' + a.dataset.cite);
        if (card) {
          ev.preventDefault();
          card.scrollIntoView({ behavior: 'smooth', block: 'center' });
          card.style.outline = '2px solid var(--accent)';
          setTimeout(() => { card.style.outline = ''; }, 1200);
        }
      });
    });
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    }
    return Promise.resolve(fallbackCopy(text));
  }
  function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) { /* ignore */ }
    document.body.removeChild(ta);
  }

  const api = { render, enhance, copyText, esc };
  global.BatsMarkdown = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
