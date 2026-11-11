/** Only an authenticated beta capability probe may choose the private draft. */
export async function betaEditorDestination(
  item: string,
  probe: (url: string) => Promise<{ mode: string }>,
): Promise<string | undefined> {
  let mode: string;
  try {
    ({ mode } = await probe(
      `/api/sc_api/beta/review_mode/${encodeURIComponent(item)}`,
    ));
  } catch (error) {
    // Missing beta drafts still use the normal editor. Auth/network errors do not.
    if ((error as { status?: number }).status === 404) return;
    throw error;
  }
  if (mode === "legacy") return;
  if (mode !== "review" && mode !== "candidate")
    throw new Error("無效的 beta 編輯模式");
  return `/admin/transcript-beta/${encodeURIComponent(item)}`;
}
