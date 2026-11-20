const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const backend = fs.readFileSync(path.join(root, 'field_work.py'), 'utf8');
const script = fs.readFileSync(path.join(root, 'static/field-work.js'), 'utf8');

test('open repair records are scoped by work order, not by the logged-in account', () => {
  assert.match(
    backend,
    /where field_repairs\.order_id = \? and field_repairs\.completed_date is null/,
    'the open-repair list must filter by order and completion only',
  );
  assert.doesNotMatch(
    backend,
    /from field_repairs where order_id = \? and user_id = \? and completed_date is null/,
    'the open-repair list must not hide repairs created by another repairer',
  );
  assert.match(backend, /users\.name as owner_name/);
});

test('a new repairer can continue and finish another repairer\'s record', () => {
  assert.doesNotMatch(backend, /where id = \? and order_id = \? and user_id = \?/);
  assert.doesNotMatch(backend, /where id = \? and order_id = \? and user_id = \? and completed_date is null/);
  assert.doesNotMatch(
    backend,
    /where order_id = \? and equipment_session = \? and photo_type = 'equipment' and user_id = \?/,
  );
});

test('the pending-repair picker marks repairs owned by another repairer', () => {
  assert.match(script, /row\.is_mine === false && row\.owner_name/);
  assert.match(script, /fieldText\('维修人员'\)/);
});

test('after-repair capture defaults to the selected technician and drops the old owner', () => {
  assert.match(script, /repair\.is_mine === false/);
  assert.match(script, /\$\('technicianSelect'\)\.value === String\(repair\.user_id\)/);
  assert.match(script, /\$\('technicianSelect'\)\.value = String\(profile\.user\.id\)/);
  assert.match(script, /const technician = profile\.technicians\.find\(person => String\(person\.id\) === \$\('technicianSelect'\)\.value\)/);
});
