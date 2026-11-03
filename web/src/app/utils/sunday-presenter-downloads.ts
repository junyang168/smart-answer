import { promises as fs } from "fs";
import path from "path";

export const presenterAssets = {
  "presenter-mac-arm64": { prefix: "Sunday-Presenter-", suffix: "-mac-arm64.dmg", type: "application/x-apple-diskimage" },
  "remote-mac-universal": { prefix: "Sunday-Remote-", suffix: "-mac-universal.zip", type: "application/zip" },
} as const;

export async function loadPresenterRelease() {
  if (!process.env.DATA_BASE_DIR) throw new Error("Presenter downloads unavailable");
  const root = await fs.realpath(path.join(process.env.DATA_BASE_DIR, "sunday-presenter", "releases"));
  const manifestPath = path.join(root, "latest.json");
  const stat = await fs.lstat(manifestPath);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 32768) throw new Error("Invalid release manifest");
  const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
  if (manifest.schemaVersion !== 1 || manifest.status !== "pilot" ||
      typeof manifest.version !== "string" || !/^\d+\.\d+\.\d+$/.test(manifest.version) ||
      !Array.isArray(manifest.artifacts) || manifest.artifacts.length !== 2) throw new Error("Invalid release manifest");
  const versionRoot = path.join(root, manifest.version);
  if ((await fs.lstat(versionRoot)).isSymbolicLink() || await fs.realpath(versionRoot) !== versionRoot) throw new Error("Invalid release directory");
  const artifacts = await Promise.all(Object.entries(presenterAssets).map(async ([id, spec]) => {
    const entry = manifest.artifacts.find((item: { id?: unknown }) => item?.id === id);
    const filename = `${spec.prefix}${manifest.version}${spec.suffix}`;
    if (!entry || entry.filename !== filename || !Number.isSafeInteger(entry.bytes) || entry.bytes <= 0 ||
        typeof entry.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(entry.sha256)) throw new Error("Invalid release artifact");
    const file = path.join(versionRoot, filename);
    const fileStat = await fs.lstat(file);
    if (fileStat.isSymbolicLink() || !fileStat.isFile() || fileStat.size !== entry.bytes) throw new Error("Release artifact unavailable");
    return { id, filename, bytes: entry.bytes as number, sha256: entry.sha256 as string, file, type: spec.type };
  }));
  return { version: manifest.version as string, status: "pilot" as const, artifacts };
}
