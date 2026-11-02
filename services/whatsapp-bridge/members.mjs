import { normalizeSenderId } from "./sender.mjs";

// Contact labels are review hints only. Only WhatsApp's LID/PN lookup links IDs.
export async function readMembers(client, groups) {
  const participants = new Map();
  for (const group of groups) {
    const chat = await client.getChatById(group);
    if (!chat.isGroup || !Array.isArray(chat.participants)) throw new Error("Cannot read selected group members");
    for (const participant of chat.participants) {
      const id = normalizeSenderId(participant.id?._serialized);
      if (!id) throw new Error("Cannot safely identify a selected group participant");
      if (!participants.has(id)) participants.set(id, { id, groups: [] });
      participants.get(id).groups.push(group);
    }
  }
  if (participants.size > 500) throw new Error("Selected member roster exceeds 500 accounts");
  const members = [];
  // Bounded batches avoid overloading the connected client.
  const entries = [...participants.values()];
  for (let offset = 0; offset < entries.length; offset += 5) {
    members.push(...await Promise.all(entries.slice(offset, offset + 5).map(async member => {
      const row = { ...member, ids: [member.id], contact_name: "", push_name: "", is_my_contact: false, error: null };
      try {
        const pairs = await client.getContactLidAndPhone([member.id]);
        const pair = pairs[0];
        const ids = [...new Set([normalizeSenderId(pair?.lid), normalizeSenderId(pair?.pn)].filter(Boolean))];
        if (ids.includes(member.id) && ids.some(x => x.endsWith("@lid")) && ids.some(x => x.endsWith("@c.us"))) row.ids = ids.sort();
        else row.error = "WhatsApp did not provide a verified LID/phone pair";
      } catch { row.error = "WhatsApp account ID lookup unavailable"; }
      try {
        const contact = await client.getContactById(member.id);
        row.contact_name = contact.name || "";
        row.push_name = contact.pushname || "";
        row.is_my_contact = contact.isMyContact === true;
      } catch { row.error = row.error || "Contact label unavailable"; }
      return row;
    })));
  }
  return { groups, members, checked_at: new Date().toISOString() };
}
