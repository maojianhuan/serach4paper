/* A normal content browser, deliberately outside Zotero's basicViewer link handler. */
var IEEELogin = {
  browsers: [],
  get browser() { return this.browsers[this.browsers.length - 1]; },
  init() {
    this.userContextId = window.arguments[0].userContextId;
    window.browserDOMWindow = {
      QueryInterface: ChromeUtils.generateQI(["nsIBrowserDOMWindow"]),
      // Gecko loads these new contexts itself, preserving POST data and window.opener.
      createContentWindow(uri, info) { return IEEELogin.createBrowser(info).browsingContext; },
      createContentWindowInFrame(uri, params) { return IEEELogin.createBrowser(params.openWindowInfo); },
      openURI(uri, info, where, flags, principal, csp) {
        return IEEELogin.openPopup(uri, info, { triggeringPrincipal: principal, csp }).browsingContext;
      },
      openURIInFrame(uri, params) {
        return IEEELogin.openPopup(uri, params.openWindowInfo, {
          triggeringPrincipal: params.triggeringPrincipal, csp: params.csp, referrerInfo: params.referrerInfo
        });
      },
      canClose() { return true; },
      get tabCount() { return IEEELogin.browsers.length; }
    };
    document.getElementById("back").addEventListener("command", () => {
      if (this.browser.canGoBack) this.browser.goBack();
    });
    document.getElementById("close-popup").addEventListener("command", () => this.closeBrowser(this.browser));
    const browser = this.createBrowser();
    browser.loadURI(Services.io.newURI("https://ieeexplore.ieee.org/"), {
      triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal()
    });
  },
  createBrowser(openWindowInfo) {
    if (openWindowInfo && openWindowInfo.originAttributes.userContextId !== this.userContextId) {
      throw new Error("IEEE 登录弹出页的 Cookie 上下文不一致。");
    }
    const browser = document.createXULElement("browser");
    browser.setAttribute("type", "content");
    browser.setAttribute("flex", "1");
    browser.setAttribute("remote", openWindowInfo?.isRemote ? "true" : "false");
    browser.setAttribute("maychangeremoteness", "true");
    browser.setAttribute("disableglobalhistory", "true");
    // Must be set before insertion, when Gecko creates the browsing context.
    browser.setAttribute("usercontextid", String(this.userContextId));
    browser.setAttribute("messagemanagergroup", "search4paper-ieee-login");
    if (openWindowInfo) browser.openWindowInfo = openWindowInfo;
    if (this.browser) this.browser.hidden = true;
    this.browsers.push(browser);
    document.getElementById("login-content").appendChild(browser);
    browser.browsingContext.sandboxFlags &= ~0x80; // Allow page JavaScript, as in Zotero's viewer.
    const progress = {
      QueryInterface: ChromeUtils.generateQI(["nsIWebProgressListener", "nsISupportsWeakReference"]),
      onLocationChange() { if (browser === IEEELogin.browser) IEEELogin.updateSite(); },
      onStateChange() {}, onProgressChange() {}, onStatusChange() {}, onSecurityChange() {}, onContentBlockingEvent() {}
    };
    browser.addProgressListener(progress, Ci.nsIWebProgress.NOTIFY_LOCATION);
    browser.ieeeProgressListener = progress;
    browser.addEventListener("DOMWindowClose", event => {
      if (!event.isTrusted) return;
      event.preventDefault();
      this.closeBrowser(browser);
    });
    this.updateSite();
    return browser;
  },
  openPopup(uri, info, options) {
    const browser = this.createBrowser(info);
    browser.loadURI(uri, options);
    return browser;
  },
  closeBrowser(browser) {
    if (browser === this.browsers[0]) { window.close(); return; }
    this.browsers.splice(this.browsers.indexOf(browser), 1);
    browser.remove();
    this.browser.hidden = false;
    this.updateSite();
  },
  updateSite() {
    const uri = this.browser.currentURI;
    // Show only the origin, never SAML query strings or other session parameters.
    document.getElementById("site").value = uri?.schemeIs("https") || uri?.schemeIs("http")
      ? uri.prePath : "正在加载登录页面…";
    document.getElementById("close-popup").hidden = this.browsers.length < 2;
    document.getElementById("back").disabled = !this.browser.canGoBack;
  }
};
window.addEventListener("load", () => IEEELogin.init(), { once: true });
