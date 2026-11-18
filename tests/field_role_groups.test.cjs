const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'templates/field_work.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'static/field-work.js'), 'utf8');

test('notes and work content stay on the capture screen and uploads can request a watermark', () => {
  const device = html.match(/<fieldset id="deviceSession"[^>]*>/)[0];
  assert.doesNotMatch(device, /\shidden(?:\s|>)/);
  assert.match(html, /id="photoNote"/);
  assert.match(html, /id="repairWork"/);
  assert.match(html, /id="repairFault"/);
  assert.match(html, /备注 \/ 工作内容/);
  assert.match(script, /\$\('photoNote'\)\.value\.trim\(\)/);
  assert.match(script, /\$\('repairWork'\)\.value\.trim\(\)/);
  assert.match(script, /awaiting_watermark/);
  assert.match(script, /applyGroupWatermark/);
  assert.match(script, /client_watermarked/);
  // 重试上传直接进入上传队列，不再先弹本组确认窗口。
  assert.match(script, /\$\('retryUpload'\)\.addEventListener\('click', \(\) => \{ syncQueue\(\); \}\)/);
  const upload = script.split('async function syncQueue')[1].split('async function completeBatch')[0];
  const guard = upload.indexOf('awaiting_watermark === true');
  const append = upload.indexOf("data.append('photo'");
  assert.ok(guard >= 0 && append > guard);
  // 待水印草稿在重试时沿用已生成的预览水印，清除标记后继续上传，不再 continue 阻塞。
  assert.match(upload.slice(guard, append), /photo\.awaiting_watermark = false/);
  assert.doesNotMatch(upload.slice(guard, append), /continue/);
  assert.match(script, /unionGroupFields/);
});

test('the type selector is not shown before shooting', () => {
  const fieldset = html.match(/<fieldset id="captureKind"[^>]*>/)[0];
  assert.match(fieldset, /\shidden(?:\s|>)/);
  assert.match(html, /id="shutterButton"/);
  assert.match(html, /id="cameraBar"/);
  assert.doesNotMatch(html, /id="shutterFile"/);
  assert.match(script, /\$\('shutterButton'\)\.addEventListener\('click', \(\) => openCamera\(false\)\)/);
  assert.match(script, /groupWatermarkTime/);
  assert.match(script, /requestTimePassword/);
  assert.match(html, /id="albumWatermark"/);
  assert.match(html, /添加水印/);
  assert.match(script, /field-album-watermark/);
  assert.match(script, /watermark_opt_out/);
  const stamp = script.split('async function applyGroupWatermark')[1].split("$('takePhoto')")[0];
  assert.match(stamp, /photo\.source === 'file' && fields\.album_watermark === false/);
  assert.match(stamp, /watermark_opt_out = true/);
  assert.match(stamp, /awaiting_watermark = false/);
  const timeSettings = html.match(/<div id="timeSettings"[^>]*>/)[0];
  assert.doesNotMatch(timeSettings, /\shidden(?:\s|>)/);
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
