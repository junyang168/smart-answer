// WhatsApp Web renamed MsgKey._serialized to $1. Keep the library's page-side
// lookups working without changing the encrypted message's actual key fields.
// Upstream: https://github.com/wwebjs/whatsapp-web.js/pull/201871
export function installMsgKeyCompatibility() {
  const prototype = window.require("WAWebMsgKey")?.prototype;
  if (!prototype) throw new Error("WhatsApp MsgKey compatibility: prototype missing");
  if (Object.getOwnPropertyDescriptor(prototype, "_serialized")) return;
  Object.defineProperty(prototype, "_serialized", {
    get() { return this.$1; },
    set(value) {
      Object.defineProperty(this, "_serialized", {
        value, writable: true, enumerable: true, configurable: true,
      });
    },
    configurable: true,
  });
}
