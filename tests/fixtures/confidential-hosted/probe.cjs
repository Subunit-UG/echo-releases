'use strict';

// No dependencies, symlinks or POSIX permission assertions: also runs on Windows ARM.
// The only upstream execution is no-build success + failure before the build command.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const root = path.resolve(__dirname, '../../..');
const action = path.join(root, '.github/actions/confidential-tauri/index.cjs');
const helper = path.join(root, 'scripts/run-confidential.sh');
const fixture = path.join(__dirname, 'entry.cjs');
const canary = 'PUBLIC_SYNTHETIC_DIAGNOSTIC_532';
const hostedResults = path.join(process.env.RUNNER_TEMP || os.tmpdir(), 'echo-public-fixture-results');

function metadata(file) {
  const text = fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n');
  const inputs = text.split('\ninputs:\n')[1].split('\noutputs:\n')[0];
  const outputs = text.split('\noutputs:\n')[1].split('\nruns:\n')[0];
  return {
    inputs: [...inputs.matchAll(/^  (\w+):$/gm)].map(m => m[1]),
    defaults: [...inputs.matchAll(/^  (\w+):\n    description:.*\n    default: '([^']*)'/gm)].map(m => [m[1], m[2]]),
    outputs: [...outputs.matchAll(/^  (\w+):$/gm)].map(m => m[1]),
    runtime: text.match(/using: '([^']+)'/)[1],
  };
}

function run(command, args, env, cwd) {
  const result = spawnSync(command, args, { env, cwd, encoding: 'utf8', timeout: 30000, windowsHide: true });
  assert.ifError(result.error);
  assert.equal(result.signal, null);
  return result;
}

function isolated(result, status, label) {
  assert.equal(result.status, status, `${label}: exit status`);
  assert.equal(result.stderr, '', `${label}: stderr`);
  const expected = status === 0 ? `Confidential ${label} succeeded.\n`
    : `::error::Confidential ${label} failed (exit ${status}); diagnostics withheld.\n`;
  assert.equal(result.stdout, expected, `${label}: public stdout allowlist`);
}

function verify(node, upstream) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'echo-public-hosted-'));
  try {
    const capture = path.join(directory, 'capture'); fs.mkdirSync(capture);
    const workspace = path.join(directory, 'workspace with spaces'); fs.mkdirSync(workspace);
    const entry = path.join(workspace, '_release-tauri/dist/index.js');
    fs.mkdirSync(path.dirname(entry), { recursive: true }); fs.copyFileSync(fixture, entry);
    // The child receives only ordinary environment plus public test values. Never pass auth.
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      !/(TOKEN|SECRET|PASSWORD|CREDENTIAL)/i.test(key) && !/^INPUT_/i.test(key)));
    Object.assign(env, {
      RUNNER_TEMP: capture, GITHUB_WORKSPACE: workspace, GITHUB_OUTPUT: path.join(directory, 'output'),
      GITHUB_ENV: path.join(directory, 'env'), PUBLIC_FIXTURE_RESULTS: directory,
      INPUT_RELEASEID: '42', INPUT_ARGS: '--public-fixture argument with spaces', INPUT_INCLUDERELEASE: 'true',
    });
    // Git Bash accepts C:/... paths; do not use symlinks or assume chmod changes NTFS ACLs.
    const bashPath = file => file.replace(/\\/g, '/');
    for (const status of [0, 37]) {
      env.PUBLIC_FIXTURE_STATUS = String(status);
      for (const boundary of ['raw', 'action', 'command']) {
        fs.writeFileSync(env.GITHUB_OUTPUT, ''); fs.writeFileSync(env.GITHUB_ENV, '');
        const result = boundary === 'command'
          ? run('bash', [bashPath(helper), bashPath(node), bashPath(entry)], env, workspace)
          : run(node, [boundary === 'raw' ? entry : action], env, workspace);
        if (boundary === 'raw') {
          assert.equal(result.status, status);
          assert.ok(result.stdout.includes(canary)); assert.ok(result.stderr.includes(canary));
          // Show a deliberately public unwrapped control in the hosted log.
          console.log(`Unwrapped ${status} control: ${canary} observed on stdout and stderr.`);
        } else isolated(result, status, boundary);
        assert.equal(fs.readFileSync(env.GITHUB_OUTPUT, 'utf8'), 'releaseId=42\n');
        assert.equal(fs.readFileSync(env.GITHUB_ENV, 'utf8'), `PUBLIC_ACTION_${status === 0 ? 'SUCCESS' : 'FAILURE'}=yes\n`);
        const forwarded = JSON.parse(fs.readFileSync(path.join(directory, `result-${status}.json`)));
        assert.equal(forwarded.releaseId, '42'); assert.equal(forwarded.args, env.INPUT_ARGS);
        assert.equal(forwarded.includeRelease, 'true');
        assert.equal(fs.realpathSync(forwarded.node), fs.realpathSync(node));
        assert.equal(fs.realpathSync(forwarded.cwd), fs.realpathSync(workspace));
        assert.deepEqual(fs.readdirSync(capture), []);
      }
    }
    if (upstream) {
      const upstreamMetadata = metadata(path.join(upstream, 'action.yml'));
      assert.deepEqual(metadata(path.join(root, '.github/actions/confidential-tauri/action.yml')), upstreamMetadata);
      // Execute the unchanged, pinned bundle. No compilation, package install or release API call.
      fs.copyFileSync(path.join(upstream, 'dist/index.js'), entry);
      for (const key of Object.keys(env)) if (key.startsWith('INPUT_')) delete env[key];
      for (const [key, value] of upstreamMetadata.defaults) env[`INPUT_${key.toUpperCase()}`] = value;
      Object.assign(env, { INPUT_PROJECTPATH: workspace, INPUT_INCLUDERELEASE: 'false',
        INPUT_INCLUDEDEBUG: 'false', INPUT_TAURISCRIPT: 'PUBLIC_NEVER_EXECUTE_BUILD',
        GITHUB_REPOSITORY: 'public/synthetic-fixture' });
      const success = run(node, [entry], env, workspace);
      assert.equal(success.status, 0); assert.ok(success.stdout.includes('No artifacts were found'));
      isolated(run(node, [action], env, workspace), 0, 'action');
      fs.mkdirSync(path.join(workspace, 'src-tauri'));
      fs.writeFileSync(path.join(workspace, 'src-tauri/tauri.conf.json'), JSON.stringify({
        productName: 'PublicFixture', version: '1.0.0', identifier: 'invalid.public.fixture',
      }));
      env.INPUT_INCLUDERELEASE = 'true'; env.INPUT_ARGS = `--config ${canary}.json`;
      const failure = run(node, [entry], env, workspace);
      assert.equal(failure.status, 1); assert.ok((failure.stdout + failure.stderr).includes(canary));
      assert.ok((failure.stdout + failure.stderr).includes('does not exist'));
      console.log(`Unwrapped upstream config control: ${canary} observed before build.`);
      isolated(run(node, [action], env, workspace), 1, 'action');
      assert.deepEqual(fs.readdirSync(capture), []);
      console.log('Pinned upstream metadata, no-build success and real pre-build config failure passed.');
    }
    console.log('Synthetic stdout/stderr, child output, status, input, env/output files and cleanup passed.');
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

switch (process.argv[2]) {
  case 'prepare': {
    const entry = path.join(root, '_release-tauri/dist/index.js');
    assert.ok(!fs.existsSync(path.join(root, '_release-tauri')), 'fixture destination must be absent');
    fs.mkdirSync(path.dirname(entry), { recursive: true }); fs.copyFileSync(fixture, entry);
    fs.mkdirSync(hostedResults);
    fs.appendFileSync(process.env.GITHUB_ENV, `PUBLIC_FIXTURE_RESULTS=${hostedResults}\n`);
    break;
  }
  case 'hosted': {
    assert.equal(process.env.PUBLIC_SUCCESS_OUTCOME, 'success');
    assert.equal(process.env.PUBLIC_FAILURE_OUTCOME, 'failure');
    assert.equal(process.env.PUBLIC_SUCCESS_OUTPUT, '42');
    assert.equal(process.env.PUBLIC_FAILURE_OUTPUT, '42');
    assert.equal(process.env.PUBLIC_ACTION_SUCCESS, 'yes');
    assert.equal(process.env.PUBLIC_ACTION_FAILURE, 'yes');
    for (const status of [0, 37]) {
      const report = JSON.parse(fs.readFileSync(path.join(hostedResults, `result-${status}.json`)));
      assert.equal(report.version.split('.')[0], '24', 'uses: must run the actual node24 action runtime');
      assert.equal(report.releaseId, '42'); assert.equal(report.args, '--public-fixture argument with spaces');
      assert.equal(report.includeRelease, 'true', 'runner must inject the action default');
      if (status === 0) {
        console.log(`Actual action runtime: Node ${report.version}, ${report.platform}/${report.arch}.`);
        verify(report.node, path.join(root, '_public-upstream'));
      }
    }
    break;
  }
  case 'local': verify(process.execPath, process.argv[3] && path.resolve(process.argv[3])); break;
  default: throw new Error('Expected prepare, hosted or local');
}
