/** Manual inspection belongs to the current editing paragraph, never a global pin. */
export type SlideInspection = { paragraphId: string; groupId: string };
type ReferenceGroup = {
  id: string;
  before_paragraph_id: string | null;
  hidden: boolean;
  needs_review: boolean;
};

export function selectReferenceGroup<T extends ReferenceGroup>(
  groups: readonly T[],
  paragraphId: string,
  inspection?: SlideInspection,
): T | undefined {
  if (inspection?.paragraphId === paragraphId) {
    const explicit = groups.find((group) => group.id === inspection.groupId);
    if (explicit) return explicit;
  }
  // Uncertain or hidden occurrences remain inspectable, but are not automatic
  // reading references. In particular, a brief skipped frame must not displace
  // the stable representative assigned to the same paragraph.
  return groups.find(
    (group) =>
      group.before_paragraph_id === paragraphId &&
      !group.hidden &&
      !group.needs_review,
  );
}
