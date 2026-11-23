const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'templates/field_work.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'static/field-work.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static/field-work.css'), 'utf8');
const worker = fs.readFileSync(path.join(root, 'static/field-sw.js'), 'utf8');

const pickers = ['faultAlbum', 'nameplateAlbum', 'nameplateCamera', 'cameraAlbum', 'repairAlbum'];

test('album inputs are tappable on iOS and accept HEIC', () => {
  assert.match(css, /\[hidden\]\{display:none!important\}/);
  for (const id of pickers) {
    const tag = html.match(new RegExp(`<input\\b[^>]*\\bid="${id}"[^>]*>`));
    assert.ok(tag, id);
    assert.doesNotMatch(tag[0], /\shidden(?:\s|=|>)/, id);
    assert.match(tag[0], /class="ios-file-input"/, id);
    assert.match(tag[0], /accept="image\/\*,\.heic,\.heif,\.avif"/, id);
    assert.match(script, new RegExp(`chosenFiles\\(\\$\\('${id}'\\)\\)`), id);
  }
  for (const id of ['faultAlbum', 'nameplateAlbum', 'cameraAlbum', 'repairAlbum']) {
    assert.doesNotMatch(script, new RegExp(`\\$\\('${id}'\\)\\.click\\(\\)`), id);
  }
  assert.match(script, /input\.value = ''/);
  assert.match(script, /imageOrientation:\s*'from-image'/);
  assert.match(worker, /prasinos-field-92/);
  assert.doesNotMatch(script, /\$\('nameplateCamera'\)\.click\(\)/);
  for (const id of ['retryRecognitionFile', 'retrySamePhotoFile']) {
    const retry = html.match(new RegExp(`<input\\b[^>]*\\bid="${id}"[^>]*>`));
    assert.ok(retry, id);
    assert.match(retry[0], /class="ios-file-input"/);
    assert.match(retry[0], /capture="environment"/);
    assert.doesNotMatch(retry[0], /\shidden(?:\s|=|>)/);
  }
});

test('recognition shows a cancelable wait state for the nameplate and the fault screen', () => {
  assert.match(html, /正在识别铭牌，请稍候…/);
  assert.match(html, /id="recognizeOverlayElapsed"/);
  assert.match(html, /id="cancelRecognize"/);
  assert.match(html, /id="overlayRetry"/);
  assert.match(html, /id="overlayManual"/);
  assert.match(script, /正在识别铭牌，请稍候…/);
  assert.match(script, /正在识别故障屏幕，请稍候…/);
  assert.match(script, /已等待 /);
  assert.match(script, /识别超时/);
  assert.match(script, /lockCapture\(true\)/);
  assert.match(css, /recognize-spin/);
});
