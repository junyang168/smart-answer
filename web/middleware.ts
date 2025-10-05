import { withAuth } from "next-auth/middleware";
import { NextResponse } from "next/server";

export default withAuth(
  function middleware(req) {
    const token = req.nextauth.token;
    const pathname = req.nextUrl.pathname;

    const isApiRequest = pathname.startsWith("/api/");

    if (token?.role === "editor") {
      return NextResponse.next();
    }

    if (!token) {
      if (isApiRequest) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
      }
      const signInUrl = new URL("/api/auth/signin", req.url);
      signInUrl.searchParams.set("callbackUrl", req.url);
      return NextResponse.redirect(signInUrl);
    }

    if (isApiRequest) {
      return NextResponse.json({ error: "Forbidden" }, { status: 403 });
    }

    return NextResponse.redirect(new URL("/", req.url));
  },
  {
    callbacks: {
      authorized: ({ token }) => !!token,
    },
  },
);

export const config = {
  matcher: ["/admin/:path*", "/api/admin/:path*"],
};
