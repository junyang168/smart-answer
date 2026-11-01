import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { installMsgKeyCompatibility } from "./msg-key-compat.mjs";

const filename = new URL("./node_modules/whatsapp-web.js/src/util/Injected/Utils.js", import.meta.url);
const marker = "// sunday-assistant MsgKey compatibility v1";
const source = readFileSync(filename, "utf8");
if (!source.includes(marker)) {
  const hash = createHash("sha256").update(source).digest("hex");
  if (hash !== "0d0f88565f481dbfeb9493b04b24033a2cb60f5fd2fd0e84e543b461d98878fe") throw new Error("Unexpected whatsapp-web.js Utils.js; review compatibility patch before upgrading");
  let patched = source.replace("    window.WWebJS = {};", `    window.WWebJS = {};\n    ${marker}\n    (${installMsgKeyCompatibility.toString()})();`);
  // serialize() returns plain objects: preserve the canonical ID explicitly
  // rather than depending on an inherited getter surviving serialization.
  patched = patched.replace("        const msg = message.serialize();", `        const msg = message.serialize();
        msg.id = { ...msg.id, _serialized: msg.id._serialized || msg.id.$1 || message.id._serialized };
        for (const field of ['from', 'to', 'author']) {
            if (msg[field] && typeof msg[field] === 'object') {
                msg[field] = msg[field]._serialized || msg[field].$1;
            }
        }`);
  writeFileSync(filename, patched);
}
