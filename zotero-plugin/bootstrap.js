var chromeHandle;
var searchWindow;
var ieeeSession;
var acmSession;
var pluginRootURI;
var welcomeWindow;
var pluginActive = false;

function install() {}
function uninstall() {}

async function startup({ rootURI }) {
  await Zotero.initializationPromise;
  chromeHandle = Cc["@mozilla.org/addons/addon-manager-startup;1"]
    .getService(Ci.amIAddonManagerStartup)
    .registerChrome(Services.io.newURI(rootURI + "manifest.json"), [
      ["content", "search4paper", rootURI + "content/"]
    ]);
  ChromeUtils.registerWindowActor("IEEELoginNavigation", {
    child: { esModuleURI: "chrome://search4paper/content/IEEELoginNavigationChild.mjs",
      events: { DOMDocElementInserted: {} } },
    allFrames: true, messageManagerGroups: ["search4paper-ieee-login"]
  });
  pluginRootURI = rootURI;
  pluginActive = true;
  for (const window of Zotero.getMainWindows()) onMainWindowLoad({ window });
}

function onMainWindowLoad({ window }) {
  if (!ieeeSession) {
    const scope = { Zotero, IOUtils, PathUtils, URL, AbortController: window.AbortController };
    Services.scriptloader.loadSubScript(pluginRootURI + "content/ieee.js", scope);
    ieeeSession = new scope.Search4PaperIEEE(Zotero, { now: () => Cu.now() });
  }
  if (!acmSession) {
    const scope = { Zotero, IOUtils, PathUtils, URL, AbortController: window.AbortController };
    Services.scriptloader.loadSubScript(pluginRootURI + "content/acm.js", scope);
    acmSession = new scope.Search4PaperACM(Zotero);
  }
  const menu = window.document.createXULElement("menuitem");
  menu.id = "search4paper-open";
  menu.setAttribute("label", "search4paper：检索会议论文…");
  menu.addEventListener("command", () => {
    if (searchWindow && !searchWindow.closed) {
      searchWindow.focus();
      return;
    }
    searchWindow = window.openDialog(
      "chrome://search4paper/content/search.xhtml", "search4paper",
      "chrome,centerscreen,resizable,width=1120,height=800", { Zotero, ieeeSession, acmSession }
    );
  });
  window.document.getElementById("menu_ToolsPopup").appendChild(menu);
  showWelcome(window).catch(error => Zotero.logError(error));
}

async function showWelcome(window) {
  await Zotero.uiReadyPromise;
  // Yield to the main UI before opening a non-modal introduction.
  await new Promise(resolve => window.setTimeout(resolve, 0));
  if (!pluginActive || window.closed || (welcomeWindow && !welcomeWindow.closed)
      || Zotero.Prefs.get("extensions.search4paper.welcomeShown", true)) return;
  welcomeWindow = window.openDialog(
    "chrome://search4paper/content/welcome.xhtml", "search4paper-welcome",
    "chrome,centerscreen,resizable,width=540,height=370", { Zotero }
  );
}

function onMainWindowUnload({ window }) {
  window.document.getElementById("search4paper-open")?.remove();
}

async function shutdown() {
  pluginActive = false;
  if (welcomeWindow && !welcomeWindow.closed) welcomeWindow.close();
  welcomeWindow = null;
  if (searchWindow && !searchWindow.closed) searchWindow.close();
  searchWindow = null;
  await ieeeSession?.dispose();
  ieeeSession = null;
  await acmSession?.dispose();
  acmSession = null;
  ChromeUtils.unregisterWindowActor("IEEELoginNavigation");
  for (const window of Zotero.getMainWindows()) onMainWindowUnload({ window });
  chromeHandle?.destruct();
  chromeHandle = null;
}
