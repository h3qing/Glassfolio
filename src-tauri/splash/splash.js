// Status lines set by the app while it unlocks and starts the analysis service.
window.glassfolioStatus = (text, isError) => {
  const el = document.getElementById("status");
  el.textContent = text;
  el.className = isError ? "error" : "";
};
