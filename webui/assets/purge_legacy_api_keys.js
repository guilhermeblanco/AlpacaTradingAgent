(function purgeLegacyApiKeyStorage() {
  "use strict";

  try {
    window.localStorage.removeItem("api-keys-store");
    window.localStorage.removeItem("api-keys-store-timestamp");
    window.sessionStorage.removeItem("api-keys-store");
    window.sessionStorage.removeItem("api-keys-store-timestamp");
  } catch (_error) {
    // Storage may be unavailable under hardened browser privacy settings.
  }
})();
