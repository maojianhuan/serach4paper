var chromeHandle;
var searchWindow;

function install() {}
function uninstall() {}

async function startup({ rootURI }) {
  await Zotero.initializationPromise;
  chromeHandle = Cc["@mozilla.org/addons/addon-manager-startup;1"]
    .getService(Ci.amIAddonManagerStartup)
    .registerChrome(Services.io.newURI(rootURI + "manifest.json"), [
      ["content", "search4paper", rootURI + "content/"]
    ]);
  for (const window of Zotero.getMainWindows()) onMainWindowLoad({ window });
}

function onMainWindowLoad({ window }) {
  const menu = window.document.createXULElement("menuitem");
  menu.id = "search4paper-open";
  menu.setAttribute("label", "search4paper：检索 OpenReview 论文…");
  menu.addEventListener("command", () => {
    if (searchWindow && !searchWindow.closed) {
      searchWindow.focus();
      return;
    }
    searchWindow = window.openDialog(
      "chrome://search4paper/content/search.xhtml", "search4paper",
      "chrome,centerscreen,resizable,width=1120,height=800", { Zotero }
    );
  });
  window.document.getElementById("menu_ToolsPopup").appendChild(menu);
}

function onMainWindowUnload({ window }) {
  window.document.getElementById("search4paper-open")?.remove();
}

function shutdown() {
  if (searchWindow && !searchWindow.closed) searchWindow.close();
  searchWindow = null;
  for (const window of Zotero.getMainWindows()) onMainWindowUnload({ window });
  chromeHandle?.destruct();
  chromeHandle = null;
}
