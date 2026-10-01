/**
 * The part of the theme that runs before React: the storage key, and the
 * script the root layout inlines into <head>. Kept free of React imports so
 * the layout, a Server Component, can import it; the hook lives in theme.ts.
 */

export const THEME_STORAGE_KEY = "vista.theme";

/**
 * Runs synchronously in <head>, before the body is parsed, so the first frame
 * is already in the right theme. It also keeps every open tab in step: a
 * `storage` event fires in the *other* tabs when one changes the choice, and
 * this listener exists on every page whether or not any React component that
 * cares is mounted. Kept dependency-free and tiny; it is inlined into every
 * HTML response.
 */
export const THEME_INIT_SCRIPT = `(function(){var k=${JSON.stringify(THEME_STORAGE_KEY)},d=document.documentElement;function a(v){if(v==="light"||v==="dark")d.setAttribute("data-theme",v);else d.removeAttribute("data-theme")}try{a(localStorage.getItem(k))}catch(e){}window.addEventListener("storage",function(e){if(e.key===k)a(e.newValue)})})();`;
