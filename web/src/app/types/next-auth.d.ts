// types/next-auth.d.ts

import NextAuth, { DefaultSession, DefaultJWT } from "next-auth";
import { JWT } from "next-auth/jwt";

// 扩展 JWT token 的类型
declare module "next-auth/jwt" {
  interface JWT {
    /** 用户的角色 */
    role?: "admin" | "editor" | "admin" | "member";
    /** 内部数据库的用户 ID */
    internalId?: string;
  }
}

// 扩展 Session 对象的类型
declare module "next-auth" {
  interface Session {
    user: {
      /** 用户的角色 */
      role?: "admin" | "editor" | "viewer" | "member";
      /** 内部数据库的用户 ID */
      internalId?: string;
    } & DefaultSession["user"]; // & DefaultSession["user"] 表示合并默认的用户字段 (name, email, image)
  }
}
