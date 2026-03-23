import { NextResponse } from 'next/server';
import { promises as fs } from 'fs';
import path from 'path';
import { clearUserCache } from '@/app/utils/user-db';

const getConfigPath = () => path.join(process.cwd(), "data", "config", "config.json");

export async function GET() {
    try {
        const fileContent = await fs.readFile(getConfigPath(), 'utf-8');
        return new NextResponse(fileContent, {
            headers: { 'Content-Type': 'application/json' },
            status: 200
        });
    } catch (e: any) {
        console.error("Error reading users:", e);
        return NextResponse.json({ error: e.message }, { status: 500 });
    }
}

export async function POST(request: Request) {
    try {
        const body = await request.json();
        const { action, user, role } = body;
        
        // action: "add", "update", "delete"
        if (!['add', 'update', 'delete'].includes(action)) {
            return NextResponse.json({ error: 'Invalid action' }, { status: 400 });
        }

        const configPath = getConfigPath();
        const content = await fs.readFile(configPath, 'utf-8');
        const config = JSON.parse(content);
        
        config.users = config.users || [];
        config.user_roles = config.user_roles || [];
        config.roles = config.roles || ["admin", "editor", "reader", "reviewer"];

        const email = user.id.toLowerCase().trim();

        if (action === 'delete') {
            config.users = config.users.filter((u: any) => u.id.toLowerCase() !== email);
            config.user_roles = config.user_roles.filter((ur: any) => ur.user.toLowerCase() !== email);
        } else if (action === 'add' || action === 'update') {
            if (!email) {
                 return NextResponse.json({ error: 'Email cannot be empty' }, { status: 400 });
            }
            if (!config.roles.includes(role)) {
                 return NextResponse.json({ error: `Invalid role. Must be one of: ${config.roles.join(', ')}` }, { status: 400 });
            }

            // Update or add user object
            const existingUserIndex = config.users.findIndex((u: any) => u.id.toLowerCase() === email);
            if (existingUserIndex >= 0) {
                config.users[existingUserIndex].name = user.name;
            } else {
                config.users.push({ id: email, name: user.name });
            }

            // Update or add role object
            const existingRoleIndex = config.user_roles.findIndex((ur: any) => ur.user.toLowerCase() === email);
            if (existingRoleIndex >= 0) {
                config.user_roles[existingRoleIndex].role = role;
            } else {
                config.user_roles.push({ user: email, role: role });
            }
        }

        // Add 4 spaces for elegant JSON formatting (same as existing config)
        await fs.writeFile(configPath, JSON.stringify(config, null, 4), 'utf-8');
        
        // Invalidate in-memory cache
        clearUserCache();

        return NextResponse.json({ success: true, message: 'Updated successfully' });
    } catch (error: any) {
        console.error("Error updating users:", error);
        return NextResponse.json({ error: error.message }, { status: 500 });
    }
}
