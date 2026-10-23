'use strict';

// Isolate the entire pinned action, including getInfo/config parsing before build.
// Running with this action's Node preserves the runner's node24 action runtime;
// setup-node's product toolchain and all INPUT_*/GITHUB_* file channels pass through.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawnSync } = require('node:child_process');

let directory;
let descriptor;
let status = 1;
try {
  if (!process.env.RUNNER_TEMP || !process.env.GITHUB_WORKSPACE) throw new Error();
  directory = fs.mkdtempSync(path.join(process.env.RUNNER_TEMP, 'echo-confidential-tauri-'));
  fs.chmodSync(directory, 0o700);
  descriptor = fs.openSync(path.join(directory, 'output'), 'wx', 0o600);
  const entry = path.join(process.env.GITHUB_WORKSPACE, '_release-tauri', 'dist', 'index.js');
  const child = spawnSync(process.execPath, [entry], {
    cwd: process.cwd(),
    env: process.env,
    stdio: ['inherit', descriptor, descriptor],
    windowsHide: true,
  });
  status = Number.isInteger(child.status) ? child.status
    : child.signal ? 128 + (os.constants.signals[child.signal] || 1) : 1;
} catch {
  // Never print exception text: it can include private paths or config contents.
  status = 1;
} finally {
  try {
    if (descriptor !== undefined) fs.closeSync(descriptor);
    if (directory !== undefined) fs.rmSync(directory, { recursive: true, force: true });
  } catch {
    console.log('::error::Confidential action cleanup failed.');
    if (status === 0) status = 1;
  }
}
console.log(status === 0 ? 'Confidential action succeeded.'
  : `::error::Confidential action failed (exit ${status}); diagnostics withheld.`);
process.exitCode = status;
