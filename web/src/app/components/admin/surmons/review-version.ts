// Revision identity belongs to the document, independently of HTTP compression.
export function revisionToken(value: unknown): string | undefined {
  if (typeof value !== "string") return;
  return /^(?:W\/)?"?([a-f0-9]{64})"?$/.exec(value)?.[1];
}

export function documentVersion(
  bodyVersion: unknown,
  etag: string | null,
): string {
  const version = revisionToken(bodyVersion) ?? revisionToken(etag);
  if (!version)
    throw new Error("無法取得稿件版本，請重新載入；本機修改仍保留。");
  return version;
}

export function recoverTransportVersion<
  T extends {
    baseVersion: string;
    packet?: { version: string; operation_id: string };
  },
>(saved: T): T {
  const base = revisionToken(saved.baseVersion);
  const packet = saved.packet;
  const version = revisionToken(packet?.version);
  if (
    (!base || base === saved.baseVersion) &&
    (!version || version === packet?.version)
  )
    return saved;
  // A packet carrying W/hash could never pass the server's revision comparison.
  // Retain every edit, but give the corrected request a new immutable identity.
  return {
    ...saved,
    baseVersion: base ?? saved.baseVersion,
    ...(packet && version && version !== packet.version
      ? {
          packet: { ...packet, version, operation_id: crypto.randomUUID() },
        }
      : {}),
  };
}
