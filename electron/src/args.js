// @ts-check

/**
 * @typedef {object} WindowArgs
 * @property {string} url
 * @property {boolean} dev
 * @property {boolean} smokeTest
 * @property {string | null} userDataDir
 * @property {boolean} startup
 * @property {string | null} launcher
 */

/**
 * Parse only VISTA's arguments. Electron and Chromium switches remain in argv
 * but have no effect on the application mode selected here.
 *
 * @param {string[]} argv
 * @param {NodeJS.Platform} [platform]
 * @returns {WindowArgs}
 */
export function parseArgs(argv, platform = process.platform) {
  /** @param {string} name */
  const value = (name) => {
    const hit = argv.find((argument) => argument.startsWith(`--${name}=`));
    return hit ? hit.slice(name.length + 3) : null;
  };

  const url = value('url') ?? '';
  const smokeTest = argv.includes('--smoke-test');
  const launcher = value('launcher');
  const startup =
    argv.includes('--startup') ||
    launcher !== null ||
    (platform === 'darwin' && !url && !smokeTest);

  return {
    url,
    dev: argv.includes('--dev'),
    smokeTest,
    userDataDir: value('user-data-dir'),
    startup,
    launcher,
  };
}
