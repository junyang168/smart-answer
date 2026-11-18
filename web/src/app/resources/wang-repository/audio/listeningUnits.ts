import type { Section } from './OriginalAudio';

export function parentPassage(section: Section) {
  return section.unit_passage ?? section.passage.split('-sub-', 1)[0];
}

export function groupUnits(sections: Section[]) {
  const groups: {id: string; label: string; parts: Section[]; seconds: number}[] = [];
  for (const section of sections) {
    const id = section.unit_id ?? section.passage;
    let group = groups.find(item => item.id === id);
    if (!group) {
      group = {id, label: section.unit_label ?? section.label, parts: [], seconds: 0};
      groups.push(group);
    }
    group.parts.push(section);
    group.seconds += section.sermons.reduce((sum, row) => sum + row.seconds, 0);
  }
  return groups;
}

/** Presentation only: retain stable Unit IDs in links and playback data. */
export function listeningTitle(label = '') {
  return label.replace(/^(?:Unit\s+\d+\s*[·•]\s*|\d+\.\d+\s+)/, '');
}
