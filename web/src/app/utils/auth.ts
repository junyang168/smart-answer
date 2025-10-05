import { NextAuthOptions, User, getServerSession } from "next-auth";
import { useSession } from "next-auth/react";
import { redirect, useRouter } from "next/navigation";

import GoogleProvider from "next-auth/providers/google";
import {getUserByEmail} from "./user-db"


export const authConfig: NextAuthOptions = {
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID as string,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET as string,
    })
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
      
      // 1. 登录时 (user 对象存在)
      if (user && account) {
        // 2. 根据登录用户的 email，从我们的数据库中查找附加信息
        const internalUser = await getUserByEmail(user.email);
        
        if (internalUser) {
          // 3. 将我们的自定义数据（角色和ID）附加到 JWT token 上
          const normalizedRole = internalUser.role?.toLowerCase();
          if (normalizedRole === "editor") {
            token.role = "editor";
          } else if (normalizedRole === "admin") {
            token.role = "admin";
          } else if (normalizedRole === "member") {
            token.role = "member";
          } else {
            token.role = undefined;
          }
          token.internalId = internalUser.internalId;
        }
      }
      
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
      
      // 1. 我们将 JWT token 中的自定义数据，同步到客户端可以访问的 session.user 对象上
      if (token.role && session.user) {
        session.user.role = token.role;
      }
      if (token.internalId && session.user) {
        session.user.internalId = token.internalId;
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

/*
export async function loginIsRequiredServer() {
  const session = await getServerSession(authConfig);
  if (!session) return redirect("/");
}

export function loginIsRequiredClient() {
  if (typeof window !== "undefined") {
    const session = useSession();
    const router = useRouter();
    if (!session) router.push("/");
  }
}
*/
