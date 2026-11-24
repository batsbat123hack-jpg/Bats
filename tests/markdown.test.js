/* Unit tests for static/markdown.js — run with: node tests/markdown.test.js */
const assert = require('assert');
const path = require('path');
const md = require(path.join(__dirname, '..', 'static', 'markdown.js'));

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
    console.log('  ✓', name);
  } catch (err) {
    console.error('  ✗', name);
    console.error('   ', err.message);
    process.exitCode = 1;
  }
}

console.log('markdown renderer');

test('escapes raw html', () => {
  const out = md.render('<script>alert(1)</script>');
  assert(!out.includes('<script>'), out);
  assert(out.includes('&lt;script&gt;'), out);
});

test('escapes html inside code fences', () => {
  const out = md.render('```html\n<img src=x onerror=alert(1)>\n```');
  assert(!out.includes('<img src=x'), out);
  assert(out.includes('&lt;img'), out);
});

test('headings', () => {
  assert(md.render('# Title').includes('<h1>Title</h1>'));
  assert(md.render('### Deep').includes('<h3>Deep</h3>'));
});

test('bold, italic, strike', () => {
  const out = md.render('**bold** and *it* and ~~gone~~');
  assert(out.includes('<strong>bold</strong>'), out);
  assert(out.includes('<em>it</em>'), out);
  assert(out.includes('<del>gone</del>'), out);
});

test('unordered and ordered lists', () => {
  const ul = md.render('- one\n- two');
  assert(ul.includes('<ul>') && ul.split('<li>').length === 3, ul);
  const ol = md.render('1. first\n2. second');
  assert(ol.includes('<ol>'), ol);
});

test('nested list', () => {
  const out = md.render('- a\n  - b\n  - c\n- d');
  assert(out.includes('<ul>') && out.includes('b'), out);
});

test('table renders with header and rows', () => {
  const out = md.render('| a | b |\n| --- | --- |\n| 1 | 2 |');
  assert(out.includes('<table>'), out);
  assert(out.includes('<th>a</th>'), out);
  assert(out.includes('<td>1</td>'), out);
});

test('code block has copy button and language', () => {
  const out = md.render('```python\ndef greet():\n    return "hi"\n```');
  assert(out.includes('class="code-block"'), out);
  assert(out.includes('copy-code'), out);
  assert(out.includes('python'), out);
  assert(out.includes('tk-kw'), out); // syntax highlighting applied
});

test('inline code', () => {
  assert(md.render('run `npm i` now').includes('<code>npm i</code>'));
});

test('links are sanitised to http/mailto', () => {
  const out = md.render('[x](javascript:alert(1))');
  assert(!out.includes('href="javascript:'), out);
  const good = md.render('[site](https://example.com)');
  assert(good.includes('href="https://example.com"'), good);
  assert(good.includes('rel="noopener noreferrer nofollow"'), good);
});

test('bare urls become links', () => {
  const out = md.render('see https://example.com/page for details');
  assert(out.includes('<a href="https://example.com/page"'), out);
});

test('citations become anchors', () => {
  const out = md.render('Fact [1] and more [2,3].');
  assert(out.includes('class="cite"'), out);
  assert(out.includes('data-cite="1"'), out);
  assert(out.includes('data-cite="3"'), out);
});

test('blockquote', () => {
  assert(md.render('> quoted text').includes('<blockquote>'));
});

test('horizontal rule', () => {
  assert(md.render('---').includes('<hr />'));
});

test('inline and display math are preserved', () => {
  const inline = md.render('energy $E=mc^2$ rules');
  assert(inline.includes('katex-math'), inline);
  assert(inline.includes('E=mc^2'), inline);
  const block = md.render('$$\n\\int x dx\n$$');
  assert(block.includes('math-block'), block);
});

test('task list checkboxes', () => {
  const out = md.render('- [x] done\n- [ ] todo');
  assert(out.includes('checked'), out);
  assert(out.includes('type="checkbox"'), out);
});

test('paragraph line breaks', () => {
  const out = md.render('line one\nline two');
  assert(out.includes('<br />'), out);
});

test('images render in markdown', () => {
  const out = md.render('![alt](https://example.com/a.png)');
  assert(out.includes('<img src="https://example.com/a.png"'), out);
});

test('mixed document does not crash', () => {
  const doc = [
    '## Summary',
    '',
    'Here is a list:',
    '',
    '1. First item with **bold**',
    '2. Second with `code`',
    '',
    '```js',
    'const x = 1;',
    '```',
    '',
    '| k | v |',
    '| - | - |',
    '| a | b |',
    '',
    '> quote',
    '',
    'Source [1]',
  ].join('\n');
  const out = md.render(doc);
  assert(out.includes('<h2>Summary</h2>'), out.slice(0, 200));
  assert(out.includes('<ol>'), 'ordered list');
  assert(out.includes('code-block'), 'code block');
  assert(out.includes('<table>'), 'table');
  assert(out.includes('<blockquote>'), 'quote');
  assert(out.includes('class="cite"'), 'cite');
});

test('empty input', () => {
  assert.strictEqual(md.render(''), '');
  assert.strictEqual(md.render(null), '');
});

console.log(`\n${passed} markdown tests passed`);
