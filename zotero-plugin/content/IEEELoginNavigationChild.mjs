/* Keep scripted size/toolbar popups in the login window's native browser handler. */
export class IEEELoginNavigationChild extends JSWindowActorChild {
  handleEvent() {
    const win = this.contentWindow;
    const nativeOpen = win.open;
    Cu.exportFunction((url, name, features) => {
      // Size/popup features otherwise bypass nsIBrowserDOMWindow in Gecko and open
      // Firefox's browser.xhtml, which Zotero does not provide. Preserve the two
      // security-related features; Gecko still creates/navigates the popup itself.
      const securityFeatures = String(features || "").split(",")
        .map(value => value.trim()).filter(value => /^(?:noopener|noreferrer)(?:\s*=.*)?$/i.test(value)).join(",");
      return nativeOpen.call(win, url, name, securityFeatures);
    }, win.wrappedJSObject, { defineAs: "open", allowCrossOriginArguments: true });
  }
}
