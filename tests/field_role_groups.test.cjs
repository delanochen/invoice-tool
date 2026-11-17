const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'templates/field_work.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'static/field-work.js'), 'utf8');

test('the type selector is not shown before shooting', () => {
  const fieldset = html.match(/<fieldset id="captureKind"[^>]*>/)[0];
  assert.match(fieldset, /\shidden(?:\s|>)/);
  assert.match(html, /id="shutterFile"/);
  assert.match(html, /id="shutterButton"/);
  assert.match(html, /class="capture-top"/);
});

test('the post-capture sheet lists every role and remembers the last one', () => {
  for (const label of ['进场', '离场', '自检', '铭牌', '集装箱号', '位置号', '手操器', '其他设备照片']) {
    assert.match(html, new RegExp(label));
  }
  assert.match(script, /arrival:'arrival'/);
  assert.match(script, /nameplate:'equipment'/);
  assert.match(script, /other:'equipment'/);
  assert.match(script, /field-last-photo-role/);
  assert.match(script, /function reclassifyPhoto/);
  assert.match(script, /role-badge/);
  assert.match(script, /session\.session_id \+ ':' \+ photo\.photo_type/);
});
