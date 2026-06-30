(() => {
  const loadCss = href => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = href;
    document.head.appendChild(css);
  };

  loadCss("readability-fix.css?v=8");
  loadCss("normal-app.css?v=1");

  const files = ["asset-paths.js?v=2", "app-main.js?v=1"];
  const load = (i = 0) => {
    if (i >= files.length) return;
    const script = document.createElement("script");
    script.src = files[i];
    script.async = false;
    script.onload = () => load(i + 1);
    script.onerror = () => {
      console.error("Failed to load", files[i]);
      load(i + 1);
    };
    document.body.appendChild(script);
  };
  load();
})();
