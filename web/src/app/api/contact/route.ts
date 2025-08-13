// app/api/contact/route.ts
import { NextResponse } from 'next/server';
import fs from 'fs/promises'; // 使用 Node.js 的文件系統模塊 (Promise 版本)
import path from 'path';
import nodemailer from 'nodemailer';

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

// ✅ 創建一個 nodemailer transporter
// 我們只在需要時創建它，以優化性能
const transporter = nodemailer.createTransport({
    service: 'gmail',
    auth: {
        user: process.env.EMAIL_SERVER_USER,
        pass: process.env.EMAIL_SERVER_PASSWORD,
    },
});


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

 // --- ✅ 2. 發送郵件 ---
        const mailOptions = {
            from: `"${name}" <${process.env.EMAIL_SERVER_USER}>`, // 發件人顯示為提交者的名字，但實際發件郵箱是你的服務郵箱
            replyTo: email, // 關鍵！讓教會同工可以直接“回复”到提交者的郵箱
            to: process.env.EMAIL_TO, // 收件人
            subject: `[網站聯繫表單] - ${subject}`, // 郵件主題
            // 郵件內容，可以是純文本或 HTML
            html: `
                <div style="font-family: sans-serif; line-height: 1.6;">
                    <h2 style="color: #333;">網站聯繫表單新提交</h2>
                    <p>您收到了一封來自網站聯繫表單的新信息。</p>
                    <hr style="border: none; border-top: 1px solid #eee;">
                    <p><strong>姓名:</strong> ${name}</p>
                    <p><strong>郵箱:</strong> <a href="mailto:${email}">${email}</a></p>
                    <p><strong>主題:</strong> ${subject}</p>
                    <p><strong>信息內容:</strong></p>
                    <div style="background-color: #f9f9f9; border: 1px solid #ddd; padding: 15px; border-radius: 5px;">
                        <p style="margin: 0;">${message.replace(/\n/g, '<br>')}</p>
                    </div>
                    <hr style="border: none; border-top: 1px solid #eee; margin-top: 20px;">
                    <p style="font-size: 12px; color: #888;">此郵件由達拉斯聖道教會網站自動發送。</p>
                </div>
            `,
        };

        try {
            await transporter.sendMail(mailOptions);
        } catch (mailError) {
            console.error('Mail Error:', mailError);
            // 即使郵件發送失敗，我們也認為提交是部分成功的，因為數據已保存到文件
            // 但我們可以返回一個不同的消息
            return NextResponse.json({ message: '信息已記錄，但郵件通知發送失敗。' }, { status: 207 });
        }        
        
        // 8. 返回成功的響應
        return NextResponse.json({ message: '信息提交成功！' }, { status: 201 });

    } catch (error) {
        console.error('API Error:', error);
        return NextResponse.json({ message: '服務器內部錯誤，請稍後重試。' }, { status: 500 });
    }
}