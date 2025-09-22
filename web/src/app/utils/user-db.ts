// lib/user-db.ts

// 这是一个模拟的数据库。在真实应用中，您会在这里查询您的 SQL 或 NoSQL 数据库。
const users = [
    { email: "junyang168@gmail.com", role: "admin", internalId: "junyang168@gmail.com" },
    { email: "dallas.holy.logos@gmail.com", role: "admin", internalId: "dallas.holy.logos@gmail.com" }
    // ... 添加更多用户
];

export async function getUserByEmail(email: string | null | undefined) {
    if (!email) {
        return null;
    }
    // 模拟数据库查询
    const user = users.find(u => u.email.toLowerCase() === email.toLowerCase());
    return user || null;
}