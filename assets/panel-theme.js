/* Client theme takes priority; standalone previews fall back to the OS. */
(() => {
  const root = document.documentElement;
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const params = new URLSearchParams(window.location.search);
  const valid = value => value === "light" || value === "dark";
  const previewTheme = params.get("preview") === "1" && valid(params.get("theme")) ? params.get("theme") : null;
  let hostTheme = valid(window.openai?.theme) ? window.openai.theme : null;
  let stopped = false;

  function apply() {
    if (stopped) return;
    root.dataset.theme = previewTheme || hostTheme || (media.matches ? "dark" : "light");
  }

  function dispose() {
    stopped = true;
    media.removeEventListener("change", apply);
    window.removeEventListener("pagehide", dispose);
  }

  window.executionPanelTheme = Object.freeze({
    setHostTheme(value) {
      if (valid(value)) hostTheme = value;
      else if (value === "system") hostTheme = null;
      else return;
      apply();
    },
    dispose,
  });
  media.addEventListener("change", apply);
  window.addEventListener("pagehide", dispose, { once: true });
  apply();
})();
