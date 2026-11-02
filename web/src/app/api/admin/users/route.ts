import { NextResponse } from "next/server";
import { promises as fs } from "fs";
import { clearUserCache, getUserConfigPath } from "@/app/utils/user-db";
import { getServerSession } from "next-auth";
import { authConfig } from "@/app/utils/auth";
import {
  withBetaRole,
  canManageUsers,
  assignUserRole,
} from "@/app/utils/roles";

async function authorize() {
  const session = await getServerSession(authConfig);
  if (!session?.user?.email)
    return NextResponse.json({ error: "請先登入" }, { status: 401 });
  if (!canManageUsers(session.user.email, session.user.role))
    return NextResponse.json(
      { error: "只有使用者管理員可以修改角色" },
      { status: 403 },
    );
}

const getConfigPath = getUserConfigPath;

export async function GET() {
  const denied = await authorize();
  if (denied) return denied;
  try {
    const fileContent = await fs.readFile(getConfigPath(), "utf-8");
    return new NextResponse(
      JSON.stringify(withBetaRole(JSON.parse(fileContent))),
      {
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "private, no-store",
        },
        status: 200,
      },
    );
  } catch (e: any) {
    console.error("Error reading users:", e);
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function POST(request: Request) {
  const denied = await authorize();
  if (denied) return denied;
  let sameOrigin = false;
  try {
    sameOrigin =
      new URL(request.headers.get("origin") || "").host ===
      request.headers.get("host");
  } catch {
    /* Reject missing or malformed origins. */
  }
  if (!sameOrigin)
    return NextResponse.json({ error: "請從本網站提交修改" }, { status: 403 });
  try {
    const body = await request.json();
    const { action, user, role } = body;

    // action: "add", "update", "delete"
    if (!["add", "update", "delete"].includes(action)) {
      return NextResponse.json({ error: "Invalid action" }, { status: 400 });
    }

    const configPath = getConfigPath();
    const content = await fs.readFile(configPath, "utf-8");
    const config = withBetaRole(JSON.parse(content));

    config.users = config.users || [];
    config.user_roles = config.user_roles || [];
    config.roles = config.roles || ["admin", "editor", "reader", "reviewer"];

    const email =
      typeof user?.id === "string" ? user.id.toLowerCase().trim() : "";

    if (action === "delete") {
      config.users = config.users.filter(
        (u: any) => u.id.toLowerCase() !== email,
      );
      config.user_roles = config.user_roles.filter(
        (ur: any) => ur.user.toLowerCase() !== email,
      );
    } else if (action === "add" || action === "update") {
      if (!email) {
        return NextResponse.json(
          { error: "Email cannot be empty" },
          { status: 400 },
        );
      }
      if (!config.roles.includes(role)) {
        return NextResponse.json(
          { error: `Invalid role. Must be one of: ${config.roles.join(", ")}` },
          { status: 400 },
        );
      }

      // Update or add user object
      const existingUserIndex = config.users.findIndex(
        (u: any) => u.id.toLowerCase() === email,
      );
      if (existingUserIndex >= 0) {
        config.users[existingUserIndex].name = user.name;
      } else {
        config.users.push({ id: email, name: user.name });
      }

      config.user_roles = assignUserRole(config.user_roles, email, role);
    }

    // Add 4 spaces for elegant JSON formatting (same as existing config)
    const temporary = configPath + "." + crypto.randomUUID() + ".tmp";
    await fs.writeFile(temporary, JSON.stringify(config, null, 4), {
      encoding: "utf-8",
      mode: 0o600,
    });
    await fs.rename(temporary, configPath);

    // Invalidate in-memory cache
    clearUserCache();

    return NextResponse.json({
      success: true,
      message: "Updated successfully",
    });
  } catch (error: any) {
    console.error("Error updating users:", error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
