type Review = { decision: string; sha256: string };

export function paragraphIsConfirmed(
  id: string,
  reviews: Record<string, Review>,
  shas: Record<string, string>,
  editedIds: ReadonlySet<string>,
): boolean {
  const review = reviews["p:" + id];
  return (
    !!shas[id] &&
    review?.decision === "confirmed" &&
    review.sha256 === shas[id] &&
    !editedIds.has(id)
  );
}

export function scrollToReviewParagraph(
  paragraph: HTMLElement | null,
  header: HTMLElement | null,
): void {
  if (!paragraph) return;
  // A paragraph can already be selected but off-screen. Always scroll on click.
  paragraph.style.scrollMarginTop = `${Math.max(0, header?.getBoundingClientRect().bottom ?? 0) + 16}px`;
  paragraph.scrollIntoView({ block: "start", behavior: "instant" });
}
