window.addEventListener("DOMContentLoaded", () => {
  document.getElementById("get-started").addEventListener("click", () => window.close());
}, { once: true });
window.addEventListener("unload", () => {
  window.arguments[0].Zotero.Prefs.set("extensions.search4paper.welcomeShown", true, true);
}, { once: true });
