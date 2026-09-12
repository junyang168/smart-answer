type VisualFact = {
  tag?: unknown;
  text?: unknown;
  tail?: unknown;
};

type SourceEvidence = {
  source_modality?: unknown;
  visual_locator?: unknown;
  paragraph_key?: unknown;
  visual_facts?: unknown;
  verbatim_excerpt?: unknown;
};

const LABEL_TAGS = new Set(["text", "tspan", "title", "desc"]);

function cleanLabel(value: unknown) {
  return typeof value === "string" ? value.replaceAll("\u200b", "").trim() : "";
}

/** Never render professor-displayed SVG markup as a spoken quotation. */
export function sourceEvidenceText(evidence: SourceEvidence | null | undefined) {
  if (!evidence) return "缺少逐字来源片段";
  if (evidence.source_modality !== "visual") {
    return cleanLabel(evidence.verbatim_excerpt) || "缺少逐字来源片段";
  }
  const labels: string[] = [];
  const facts = Array.isArray(evidence.visual_facts)
    ? evidence.visual_facts as VisualFact[]
    : [];
  for (const fact of facts) {
    if (!LABEL_TAGS.has(cleanLabel(fact.tag).toLowerCase())) continue;
    for (const value of [fact.text, fact.tail]) {
      const label = cleanLabel(value);
      if (label && !labels.includes(label)) labels.push(label);
    }
  }
  const locator = cleanLabel(evidence.visual_locator)
    || cleanLabel(evidence.paragraph_key)
    || "位置未知";
  return `视觉来源（非口述，${locator}）${labels.length ? `：${labels.join("；")}` : ""}`;
}
