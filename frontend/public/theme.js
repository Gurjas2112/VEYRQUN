// Applied before first paint so the page never flashes the wrong theme.
try {
  const t = localStorage.getItem("vq-theme");
  if (t) document.documentElement.dataset.theme = t;
  else if (matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.dataset.theme = "dark";
} catch { /* storage blocked: default light theme */ }
