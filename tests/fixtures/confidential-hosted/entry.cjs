'use strict';

// Deliberately PUBLIC synthetic diagnostics. Never point this fixture at source.
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const status = Number(process.env.PUBLIC_FIXTURE_STATUS);
const canary = 'PUBLIC_SYNTHETIC_DIAGNOSTIC_532';
console.log(canary);
console.error(canary);
cp.spawnSync(process.execPath, ['-e',
  `process.stdout.write('${canary}');process.stderr.write('${canary}')`], { stdio: 'inherit' });
fs.appendFileSync(process.env.GITHUB_OUTPUT, `releaseId=${process.env.INPUT_RELEASEID}\n`);
fs.appendFileSync(process.env.GITHUB_ENV, `PUBLIC_ACTION_${status === 0 ? 'SUCCESS' : 'FAILURE'}=yes\n`);
fs.writeFileSync(path.join(process.env.PUBLIC_FIXTURE_RESULTS, `result-${status}.json`), JSON.stringify({
  node: process.execPath, version: process.versions.node, platform: process.platform, arch: process.arch,
  releaseId: process.env.INPUT_RELEASEID, args: process.env.INPUT_ARGS,
  includeRelease: process.env.INPUT_INCLUDERELEASE, cwd: process.cwd(),
}));
process.exitCode = status;
