import { createHmac } from "node:crypto";
import { getServerSession } from "next-auth";
import { authConfig } from "@/app/utils/auth";

export class PublicationAuthorizationError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

export async function publicationActionHeaders(
  action: "publish" | "unpublish",
  reviewId: string,
): Promise<Headers> {
  const session = await getServerSession(authConfig);
  const actorId = session?.user?.internalId;
  const role = session?.user?.role;
  if (!actorId || (role !== "admin" && role !== "editor")) {
    throw new PublicationAuthorizationError(401, "需要负责人登录后才能执行这个操作。");
  }

  const secret = process.env.WANG_PUBLICATION_ACTION_SECRET;
  const owners = new Set(
    (process.env.WANG_PUBLICATION_OWNER_IDS ?? "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean),
  );
  if (!secret || owners.size === 0) {
    throw new PublicationAuthorizationError(503, "发布负责人验证尚未配置。");
  }
  if (!owners.has(actorId)) {
    throw new PublicationAuthorizationError(403, "只有指定负责人可以发布或撤下文章。");
  }

  const timestamp = String(Math.floor(Date.now() / 1000));
  const message = [action, reviewId, actorId, role, timestamp].join("\0");
  const signature = createHmac("sha256", secret).update(message, "utf8").digest("hex");
  return new Headers({
    "Content-Type": "application/json",
    "X-Wang-Publication-Actor": actorId,
    "X-Wang-Publication-Role": role,
    "X-Wang-Publication-Timestamp": timestamp,
    "X-Wang-Publication-Signature": signature,
  });
}
