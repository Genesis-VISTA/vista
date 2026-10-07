// @ts-check
import { spawn } from 'node:child_process';
import { closeSync, mkdirSync, openSync } from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';

import { ProtocolError, StartupStateMachine } from './startup-protocol.js';

const STOP_TIMEOUT_MS = 12_000;
// How long an exited launcher's remaining output is waited for. Bounded because
// a process the launcher left behind can hold its stdout open indefinitely.
const OUTPUT_DRAIN_MS = 250;

// What vista.cmd does before it runs vista.ps1, which the application now does
// itself (design D10). Unblock-File removes the downloaded-file mark that makes a
// RemoteSigned policy refuse the script; a command, unlike a script file, is
// not subject to execution policy, so this runs whatever the policy is. An
// AllSigned policy, which Group Policy can impose over -ExecutionPolicy
// Bypass, cannot be met by an unsigned package: exit 3 names it.
const WINDOWS_PREFLIGHT = [
  '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command',
  "Unblock-File -LiteralPath $env:VISTA_PS1 -ErrorAction SilentlyContinue; "
    + "if ((Get-ExecutionPolicy) -eq 'AllSigned') { exit 3 }",
];
const EXIT_ALL_SIGNED = 3;

/**
 * How to run the supervised launcher. vista.ps1 runs under Windows PowerShell,
 * hidden, after the preflight above; anything else is executed directly, which
 * is also how the tests' fake launcher runs.
 *
 * @param {string} launcherPath
 * @returns {{ command: string, args: string[], windowsHide: boolean, preflight: string[] | null }}
 */
export function launchCommand(launcherPath) {
  if (launcherPath.toLowerCase().endsWith('.ps1')) {
    return {
      command: 'powershell.exe',
      args: ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', launcherPath,
        '-Supervised', '-Progress', 'jsonl'],
      windowsHide: true,
      preflight: WINDOWS_PREFLIGHT,
    };
  }
  return { command: launcherPath, args: ['--supervised', '--progress=jsonl'], windowsHide: false, preflight: null };
}

/** @typedef {ReturnType<StartupStateMachine['snapshot']>} StartupSnapshot */

export class LauncherController {
  /**
   * @param {object} options
   * @param {string} options.launcherPath
   * @param {string} options.logPath
   * @param {(snapshot: StartupSnapshot) => void} options.onState
   * @param {(url: string) => void} options.onReady
   * @param {typeof spawn} [options.spawnProcess]
   * @param {NodeJS.Platform} [options.platform]
   */
  constructor({ launcherPath, logPath, onState, onReady, spawnProcess = spawn, platform = process.platform }) {
    this.launcherPath = launcherPath;
    this.platform = platform;
    this.logPath = logPath;
    this.onState = onState;
    this.onReady = onReady;
    this.spawnProcess = spawnProcess;
    this.machine = new StartupStateMachine();
    this.current = this.machine.snapshot();
    /** @type {import('node:child_process').ChildProcess | null} */
    this.child = null;
    /** @type {readline.Interface | null} */
    this.lines = null;
    /** @type {Promise<void>} */
    this.exitPromise = Promise.resolve();
    /** @type {(() => void) | null} */
    this.resolveExit = null;
    this.readySent = false;
    this.stopping = false;
  }

  start() {
    if (this.child) throw new Error('launcher is already running');
    this.machine = new StartupStateMachine();
    this.current = this.machine.snapshot();
    this.readySent = false;
    this.stopping = false;

    this.exitPromise = new Promise((resolve) => { this.resolveExit = resolve; });
    const launch = launchCommand(this.launcherPath);
    if (launch.preflight) {
      this.runPreflight(launch);
    } else {
      this.spawnLauncher(launch);
    }
  }

  /**
   * vista.ps1's preflight: its own short-lived child, which Quit can stop like
   * the launcher. Only once it succeeds does the launcher start.
   *
   * @param {ReturnType<typeof launchCommand>} launch
   */
  runPreflight(launch) {
    const log = this.openLog();
    let child;
    try {
      child = this.spawnProcess('powershell.exe', /** @type {string[]} */ (launch.preflight), {
        stdio: ['ignore', 'ignore', log],
        windowsHide: true,
        env: { ...process.env, VISTA_PS1: this.launcherPath },
      });
    } finally {
      closeSync(log);
    }
    this.child = child;
    this.emit();
    child.once('error', (error) => {
      const errorCode = /** @type {NodeJS.ErrnoException} */ (error).code ?? 'process error';
      this.launcherFailure(`Windows PowerShell could not be started (${errorCode}).`);
      this.handleExit(child, -1, null);
    });
    child.once('exit', (code, signal) => {
      if (this.child !== child) return;
      if (code === 0 && !this.stopping) {
        this.child = null;
        this.spawnLauncher(launch);
        return;
      }
      if (code === EXIT_ALL_SIGNED) {
        this.current = this.machine.failPreflight('execution-policy-all-signed');
      }
      this.handleExit(child, code, signal);
    });
  }

  openLog() {
    mkdirSync(path.dirname(this.logPath), { recursive: true });
    return openSync(this.logPath, 'a');
  }

  /** @param {ReturnType<typeof launchCommand>} launch */
  spawnLauncher(launch) {
    const log = this.openLog();
    let child;
    try {
      child = this.spawnProcess(launch.command, launch.args, {
        stdio: ['pipe', 'pipe', log],
        windowsHide: launch.windowsHide,
      });
    } finally {
      closeSync(log);
    }
    this.child = child;
    this.emit();

    if (!child.stdout) {
      this.protocolFailure('launcher stdout is unavailable');
      return;
    }
    this.lines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
    this.lines.on('line', (line) => this.acceptLine(line));
    child.once('error', (error) => {
      const errorCode = /** @type {NodeJS.ErrnoException} */ (error).code ?? 'process error';
      this.launcherFailure(`The launcher could not be started (${errorCode}).`);
      this.handleExit(child, -1, null);
    });
    child.once('exit', (code, signal) => this.afterOutput(child, () => this.handleExit(child, code, signal)));
  }

  /**
   * 'exit' can come before the launcher's last lines, usually its failed
   * event, have been read, so the exit is handled once stdout has ended.
   *
   * @param {import('node:child_process').ChildProcess} child
   * @param {() => void} done
   */
  afterOutput(child, done) {
    const stdout = child.stdout;
    if (!stdout || stdout.readableEnded || stdout.destroyed) {
      done();
      return;
    }
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      done();
    };
    const timer = setTimeout(finish, OUTPUT_DRAIN_MS);
    stdout.once('end', finish);
    stdout.once('close', finish);
  }

  /** @param {string} line */
  acceptLine(line) {
    // After a failure the launcher is only cleaning up: what it says then,
    // well-formed or not, must not replace the failure being shown.
    if (!this.child || this.current.status === 'failed') return;
    try {
      this.current = this.machine.acceptLine(line);
      this.emit();
      if (this.current.status === 'ready' && !this.readySent) {
        this.readySent = true;
        this.onReady(this.current.url);
      }
    } catch (error) {
      this.protocolFailure(error instanceof ProtocolError ? error.message : 'unknown protocol error');
    }
  }

  /** @param {string} detail */
  protocolFailure(detail) {
    if (!this.child) return;
    this.current = this.machine.failProtocol(detail);
    this.emit();
    this.child.kill('SIGTERM');
  }

  /** @param {string} message */
  launcherFailure(message) {
    this.current = this.machine.failLauncher(message);
    this.emit();
  }

  /**
   * @param {import('node:child_process').ChildProcess} child
   * @param {number | null} code
   * @param {NodeJS.Signals | null} signal
   */
  handleExit(child, code, signal) {
    if (this.child !== child) return;
    this.lines?.close();
    this.lines = null;
    this.child = null;
    this.resolveExit?.();
    this.resolveExit = null;
    if (!this.stopping && this.current.status !== 'failed') {
      const reason = signal ? `signal ${signal}` : `exit ${code ?? 'unknown'}`;
      this.current = this.machine.failLauncher(`The launcher stopped during startup (${reason}).`);
    }
    this.emit();
  }

  emit() {
    const snapshot = this.machine.snapshot();
    snapshot.canRetry = snapshot.canRetry && this.child === null;
    this.current = snapshot;
    this.onState(snapshot);
  }

  retry() {
    if (this.child || this.current.status !== 'failed' || !this.current.canRetry) return false;
    this.start();
    return true;
  }

  /** @param {number} [timeoutMs] */
  async stop(timeoutMs = STOP_TIMEOUT_MS) {
    const child = this.child;
    if (!child) return;
    this.stopping = true;
    this.current = this.machine.markStopping();
    this.emit();
    // On Windows, kill() is TerminateProcess, which runs no cleanup at all: the
    // end of stdin is vista.ps1's stop request (design D10). Elsewhere TERM
    // reaches the launcher's own trap.
    // The preflight has no stdin, and nothing to clean up.
    if (this.platform === 'win32' && child.stdin) {
      child.stdin.end();
    } else {
      child.kill('SIGTERM');
    }
    let timer;
    await Promise.race([
      this.exitPromise,
      new Promise((resolve) => { timer = setTimeout(resolve, timeoutMs); }),
    ]);
    clearTimeout(timer);
    if (this.child === child) {
      child.kill('SIGKILL');
      await this.exitPromise;
    }
  }
}
