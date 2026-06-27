import { FellowshipDocument } from "@/app/types/fellowship";

export function isMarkdownDocument(document: FellowshipDocument): boolean {
  return document.name.toLowerCase().endsWith(".md");
}

export function toFellowshipDocumentHref(date: string, document: FellowshipDocument): string {
  const encodedPath = document.name
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");

  if (!isMarkdownDocument(document)) {
    return `/sc_api/fellowships/${encodeURIComponent(date)}/documents/${encodedPath}`;
  }

  return `/resources/fellowship/${encodeURIComponent(date)}/docs/${encodedPath}`;
}
