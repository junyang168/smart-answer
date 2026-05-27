import { FellowshipDocument } from "@/app/types/fellowship";

export function isMarkdownDocument(document: FellowshipDocument): boolean {
  return document.name.toLowerCase().endsWith(".md");
}

export function toProxyDocumentUrl(url: string): string {
  return url.startsWith("/admin/") ? `/api${url}` : url;
}

export function toFellowshipDocumentHref(date: string, document: FellowshipDocument): string {
  if (!isMarkdownDocument(document)) {
    return toProxyDocumentUrl(document.url);
  }

  const encodedPath = document.name
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return `/resources/fellowship/${encodeURIComponent(date)}/docs/${encodedPath}`;
}
