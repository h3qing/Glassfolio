// The app's own local pages: status while unlocking, first-run recovery-key onboarding,
// and the "Show Recovery Key…" window. The key only ever appears here (tauri://, no network).
(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const invoke = (cmd, args) => window.__TAURI_INTERNALS__.invoke(cmd, args || {});
  const groups = (key) => key.match(/.{1,8}/g) || [];

  window.glassfolioStatus = (text, isError) => {
    const el = $("#status");
    if (!el) return;
    el.textContent = text;
    el.className = isError ? "error" : "";
  };

  function renderKey(key) {
    $$("[data-key]").forEach((box) => {
      box.replaceChildren(...groups(key).map((g, i) => {
        const span = document.createElement("span");
        span.textContent = g;
        span.title = `Group ${i + 1}`;
        return span;
      }));
    });
    const date = new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
    $$("[data-date]").forEach((el) => { el.textContent = `Created ${date}`; });
  }

  function wireKeyActions(key) {
    $$("[data-print]").forEach((b) => b.addEventListener("click", () => invoke("print_page")));
    $$("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
      await navigator.clipboard.writeText(groups(key).join(" "));
      $$("[data-copied]").forEach((n) => { n.hidden = false; });
    }));
  }

  window.glassfolioOnboard = (key) => {
    renderKey(key);
    wireKeyActions(key);
    $("#splash").hidden = true;
    $("#onboarding").hidden = false;

    const cards = $$("[data-step]");
    const dots = $$(".steps li");
    let step = 0;
    const show = (i) => {
      step = i;
      cards.forEach((c, n) => { c.hidden = n !== i; });
      dots.forEach((d, n) => d.classList.toggle("on", n <= i));
      const focus = cards[i].querySelector("input:not([type=checkbox]), button.primary");
      if (focus) focus.focus();
    };
    $$("[data-next]").forEach((b) => b.addEventListener("click", () => show(step + 1)));
    $("[data-back]").addEventListener("click", () => show(1));
    $("[data-saved]").addEventListener("change", (e) => {
      $("[data-needs-saved]").disabled = !e.target.checked;
    });

    // Ask for two different random groups, so the check needs the saved copy.
    const all = groups(key);
    const first = Math.floor(Math.random() * all.length);
    const second = (first + 1 + Math.floor(Math.random() * (all.length - 1))) % all.length;
    const [a, b] = [first, second].sort((x, y) => x - y);
    $$("[data-g1]").forEach((el) => { el.textContent = a + 1; });
    $$("[data-g2]").forEach((el) => { el.textContent = b + 1; });
    const norm = (s) => s.replace(/\s+/g, "").toLowerCase();
    $("[data-finish]").addEventListener("click", () => {
      const ok = norm($("[data-v1]").value) === all[a] && norm($("[data-v2]").value) === all[b];
      $("[data-error]").hidden = ok;
      if (!ok) return;
      $("#onboarding").hidden = true;
      $("#splash").hidden = false;
      window.glassfolioStatus("Starting the analysis service…");
      invoke("finish_onboarding");
    });
    show(0);
  };

  window.glassfolioShowKey = (key) => {
    renderKey(key);
    wireKeyActions(key);
  };
})();
