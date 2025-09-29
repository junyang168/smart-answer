// utils/user-db.ts

import path from "path";
import { promises as fs } from "fs";

type ConfigUser = {
  id: string;
  name?: string;
};

type ConfigUserRole = {
  user: string;
  role: string;
};

type CachedUser = {
  email: string;
  name?: string;
  role: string | null;
  internalId: string;
};

let cachedUsers: CachedUser[] | null = null;

async function loadUsers(): Promise<CachedUser[]> {
  if (cachedUsers) {
    return cachedUsers;
  }

  const configPath = path.join(process.cwd(), "web", "data", "config", "config.json");
  const fileContent = await fs.readFile(configPath, "utf-8");
  const config = JSON.parse(fileContent) as {
    users?: ConfigUser[];
    user_roles?: ConfigUserRole[];
  };

  const userRoles = new Map<string, string>();
  (config.user_roles ?? []).forEach((entry) => {
    userRoles.set(entry.user.toLowerCase(), entry.role);
  });

  cachedUsers = (config.users ?? []).map((user) => {
    const email = user.id.toLowerCase();
    return {
      email,
      internalId: user.id,
      name: user.name,
      role: userRoles.get(email) ?? null,
    };
  });

  return cachedUsers;
}

export async function getUserByEmail(email: string | null | undefined) {
  if (!email) {
    return null;
  }

  const users = await loadUsers();
  const lower = email.toLowerCase();
  return users.find((user) => user.email === lower) ?? null;
}
