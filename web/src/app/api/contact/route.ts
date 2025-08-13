// app/api/contact/route.ts
import { NextResponse } from 'next/server';
import fs from 'fs/promises'; // 使用 Node.js 的文件系統模塊 (Promise 版本)
import path from 'path';

// 定義數據存儲的路徑
// process.cwd() 指向項目的根目錄
const dataFilePath = path.join(process.cwd(), 'data', 'contact-submissions.json');

// 定義接收數據的類型
interface ContactSubmission {
    name: string;
    email: string;
    subject: string;
    message: string;
    submittedAt: string; // 我們會添加一個提交時間戳
}

// 確保數據文件夾存在
async function ensureDirectoryExists() {
    try {
        await fs.mkdir(path.dirname(dataFilePath), { recursive: true });
    } catch (error) {
        console.error("Error creating data directory:", error);
    }
}

export async function POST(request: Request) {
    try {
        // 1. 解析請求體中的 JSON 數據
        const body = await request.json();
        const { name, email, subject, message } = body;

        // 2. 進行基本的服務器端驗證
        if (!name || !email || !subject || !message) {
            return NextResponse.json({ message: '所有字段均為必填項。' }, { status: 400 });
        }

        // 3. 確保數據文件夾存在
        await ensureDirectoryExists();

        // 4. 準備新的提交數據
        const newSubmission: ContactSubmission = {
            name,
            email,
            subject,
            message,
            submittedAt: new Date().toISOString(),
        };

        // 5. 讀取現有的 JSON 文件
        let submissions: ContactSubmission[] = [];
        try {
            const fileContent = await fs.readFile(dataFilePath, 'utf-8');
            submissions = JSON.parse(fileContent);
        } catch (error: any) {
            // 如果文件不存在，這是一個預期內的錯誤，我們將創建一個新文件
            if (error.code !== 'ENOENT') {
                throw error; // 如果是其他錯誤，則拋出
            }
        }
        
        // 6. 將新的提交信息追加到數組的開頭
        submissions.unshift(newSubmission);

        // 7. 將更新後的數組寫回 JSON 文件
        // JSON.stringify 的第三個參數 '2' 是為了讓 JSON 文件格式化，更易讀
        await fs.writeFile(dataFilePath, JSON.stringify(submissions, null, 2), 'utf-8');
        
        // 8. 返回成功的響應
        return NextResponse.json({ message: '信息提交成功！' }, { status: 201 });

    } catch (error) {
        console.error('API Error:', error);
        return NextResponse.json({ message: '服務器內部錯誤，請稍後重試。' }, { status: 500 });
    }
}