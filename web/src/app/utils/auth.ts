import { NextAuthOptions, getServerSession } from "next-auth";

import { normalizeRole } from "./roles";
import GoogleProvider from "next-auth/providers/google";
import { getUserByEmail } from "./user-db";

export const authConfig: NextAuthOptions = {
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID as string,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET as string,
    }),
  ],

  // ✅ 添加 callbacks 配置
  callbacks: {
    /**
     * @param  {object}  token     Decrypted JSON Web Token
     * @param  {object}  user      User object from the provider (e.g., Google) - only available on sign in
     * @param  {object}  account   Provider account (e.g., Google account)
     * @return {object}            The JWT that will be saved in the cookie
     */
    async jwt({ token, user, account }) {
      // 这个回调在 JWT 被创建或更新时触发（例如，在登录时）

      const internalUser = await getUserByEmail(user?.email || token.email);
      token.role = normalizeRole(internalUser?.role);
      token.internalId = internalUser?.internalId;

      // 4. 返回更新后的 token
      return token;
    },

    /**
     * @param  {object}  session   The session object that will be passed to the client
     * @param  {object}  token     The JWT token from the `jwt` callback
     * @return {object}            The session object
     */
    async session({ session, token }) {
      // 这个回调在 session 被访问时触发（例如，客户端调用 useSession()）

      // DEV ENVIRONMENT: Hardcode role to 'editor' when Google Auth is not working
      if (process.env.NODE_ENV === "development") {
        if (session.user) {
          session.user.role = "editor";
          session.user.internalId = "dev-user";
        }
        return session;
      }

      // Re-read current roles; a signed-in tester can be revoked without token expiry.
      if (session.user) {
        const user = await getUserByEmail(session.user.email);
        session.user.role = normalizeRole(user?.role);
        session.user.internalId = user?.internalId;
      }

      // 2. 返回更新后的 session 对象
      return session;
    },
  },

  // 确保您使用的是 'jwt' session 策略，这是默认的
  session: {
    strategy: "jwt",
  },
};

/**
 * Get session with dev environment fallback
 * In development, returns a mock editor session if no real session exists
 */
export async function getSessionWithDevFallback() {
  const session = await getServerSession(authConfig);

  // If we have a real session, return it
  if (session) {
    return session;
  }

  // In development, return a mock editor session
  if (process.env.NODE_ENV === "development") {
    return {
      user: {
        name: "Dev User",
        email: "dev@example.com",
        role: "editor" as const,
        internalId: "dev-user",
      },
      expires: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(), // 24 hours from now
    };
  }

  // In production, return null if no session
  return null;
}
