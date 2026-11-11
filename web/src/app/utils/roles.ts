export type UserRole =
  | "admin"
  | "editor"
  | "beta_tester"
  | "reader"
  | "reviewer"
  | "member"
  | "viewer";
export const BETA_TESTER_ROLE = "beta_tester";
export function normalizeRole(role: unknown): UserRole | undefined {
  return typeof role === "string" &&
    [
      "admin",
      "editor",
      "beta_tester",
      "reader",
      "reviewer",
      "member",
      "viewer",
    ].includes(role.toLowerCase())
    ? (role.toLowerCase() as UserRole)
    : undefined;
}
export function hasEditorAccess(
  role: unknown,
): role is "admin" | "editor" | "beta_tester" {
  return ["admin", "editor", "beta_tester"].includes(String(role));
}
export function hasTranscriptBetaAccess(
  email?: string | null,
  role?: unknown,
): boolean {
  return (
    email?.toLowerCase() === "junyang168@gmail.com" || role === BETA_TESTER_ROLE
  );
}
export function canManageUsers(email?: string | null, role?: unknown): boolean {
  return (
    !!email &&
    (role === "admin" ||
      (email.toLowerCase() === "junyang168@gmail.com" && hasEditorAccess(role)))
  );
}
export function roleLabel(role: string): string {
  return role === BETA_TESTER_ROLE ? "beta tester" : role;
}

export function withBetaRole<
  T extends {
    roles?: string[];
    role_permissions?: { role: string; permission: string }[];
  },
>(config: T): T {
  config.roles = [
    ...new Set([
      ...(config.roles || ["admin", "editor", "reader", "reviewer"]),
      BETA_TESTER_ROLE,
    ]),
  ];
  const permissions = config.role_permissions || [];
  config.role_permissions = [
    ...permissions.filter((entry) => entry.role !== BETA_TESTER_ROLE),
    ...permissions
      .filter((entry) => entry.role === "editor")
      .map((entry) => ({ ...entry, role: BETA_TESTER_ROLE })),
  ];
  return config;
}

export function assignUserRole(
  entries: { user: string; role: string }[],
  email: string,
  role: string,
) {
  // The management form assigns one role. Remove stale duplicates on revocation.
  return [
    ...entries.filter(
      (entry) => entry.user.toLowerCase() !== email.toLowerCase(),
    ),
    { user: email, role },
  ];
}
