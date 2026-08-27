import { createReadStream, statSync } from "node:fs";
import { Readable } from "node:stream";
import { join, normalize } from "node:path";
import { NextRequest } from "next/server";

/**
 * 開發時提供錄音，支援 range request。
 *
 * 生產環境用不到：nginx 的 `location /web/` 直接從磁碟提供，請求根本到不了
 * Next。這條只為了讓卡片預覽能聽見聲音。
 *
 * 兩條路都走不通，才有這一條：Next 的 dev rewrite 會把整個檔案緩衝完才回應
 * （59 MB 的 mp3 等八秒仍然 `buffered: null`）；直接連 nginx:8888 則跨來源被
 * CORS 擋下，而加 CORS 標頭要 root 重載 nginx。
 *
 * 路徑刻意不放在 `/api/` 底下：`next.config.mjs` 有一條 `/api/:path*` 的
 * catch-all rewrite，它優先於路由處理器，會把請求轉給 FastAPI——放進去就是 404。
 */

const MEDIA_ROOT = "/opt/homebrew/var/www/church/web/video";

/** Node 的檔案流轉成 Web 流。
 *
 * 直接把 Node stream 當成 `ReadableStream` 塞進 `Response`，型別上騙得過去，實
 * 際上 Next 只能整個緩衝——59 MB 的 mp3 於是永遠停在 readyState 0，而且不報錯。
 */
const toWeb = (stream: ReturnType<typeof createReadStream>) =>
  Readable.toWeb(stream) as unknown as ReadableStream;

export async function GET(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  // 檔名來自 URL，所以要擋住 `..`：normalize 之後必須仍在 MEDIA_ROOT 之下。
  const file = normalize(join(MEDIA_ROOT, ...path.map(decodeURIComponent)));
  if (!file.startsWith(MEDIA_ROOT)) return new Response("no", { status: 400 });

  let size: number;
  try {
    size = statSync(file).size;
  } catch {
    return new Response("not found", { status: 404 });
  }

  const type = file.endsWith(".mp4") ? "video/mp4" : "audio/mpeg";
  const range = request.headers.get("range");
  if (!range) {
    return new Response(toWeb(createReadStream(file)), {
      headers: { "content-type": type, "content-length": String(size), "accept-ranges": "bytes" },
    });
  }
  const [rawStart, rawEnd] = range.replace("bytes=", "").split("-");
  const start = Number(rawStart) || 0;
  const end = rawEnd ? Math.min(Number(rawEnd), size - 1) : size - 1;
  return new Response(toWeb(createReadStream(file, { start, end })), {
    status: 206,
    headers: {
      "content-type": type,
      "content-length": String(end - start + 1),
      "content-range": `bytes ${start}-${end}/${size}`,
      "accept-ranges": "bytes",
    },
  });
}
