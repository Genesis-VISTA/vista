// @ts-check

export const EXIT_ALREADY_OPEN = 75;

/**
 * Acquire the lock for every visible application mode. Smoke tests remain
 * independent so release validation cannot be satisfied by an already-open
 * application.
 *
 * @param {{ requestSingleInstanceLock(): boolean }} application
 * @param {{ smokeTest: boolean }} args
 * @returns {boolean}
 */
export function acquireSingleInstanceLock(application, args) {
  return args.smokeTest || application.requestSingleInstanceLock();
}

/**
 * @param {{ isMinimized(): boolean, restore(): void, show(): void, focus(): void } | null} window
 */
export function focusWindow(window) {
  if (!window) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}
