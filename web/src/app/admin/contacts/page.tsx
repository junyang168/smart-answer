import fs from 'fs/promises';
import path from 'path';
import Link from 'next/link';
import { ArrowLeft, User, Mail, Clock, MessageSquare, Download } from 'lucide-react';

interface ContactSubmission {
    name: string;
    email: string;
    subject: string;
    message: string;
    submittedAt: string;
}

export default async function ContactsPage() {
    let submissions: ContactSubmission[] = [];
    const dataFilePath = path.join(process.cwd(), 'data', 'contact-submissions.json');

    try {
        const fileContent = await fs.readFile(dataFilePath, 'utf-8');
        submissions = JSON.parse(fileContent);
    } catch (error: any) {
        if (error.code !== 'ENOENT') {
            console.error("Failed to read contact submissions:", error);
        }
        // If file doesn't exist, it's expected if no submissions occurred yet.
    }

    return (
        <div className="min-h-screen bg-gray-50 py-12">
            <div className="mx-auto max-w-5xl px-6">
                <header className="mb-8 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                    <div>
                        <h1 className="text-3xl font-bold text-gray-900">新朋友信息</h1>
                        <p className="mt-2 text-gray-600">查看網站首頁「聯絡我們」表單的提交紀錄</p>
                    </div>
                    <Link
                        href="/admin"
                        className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 transition self-start md:self-auto"
                    >
                        <ArrowLeft className="h-4 w-4" />
                        返回管理後台
                    </Link>
                </header>

                {submissions.length === 0 ? (
                    <div className="rounded-xl border border-gray-200 bg-white p-12 text-center shadow-sm">
                        <MessageSquare className="mx-auto h-12 w-12 text-gray-300" />
                        <h3 className="mt-4 text-lg font-medium text-gray-900">尚無訊息</h3>
                        <p className="mt-2 text-sm text-gray-500">目前還沒有從網站表單提交的新朋友信息。</p>
                    </div>
                ) : (
                    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
                        <div className="border-b border-gray-200 bg-gray-50/50 px-6 py-4 flex items-center justify-between">
                            <h2 className="text-sm font-medium text-gray-700">共 {submissions.length} 筆紀錄</h2>
                            <a 
                                href="/data/contact-submissions.json" 
                                download
                                className="hidden items-center gap-1.5 text-sm font-medium text-blue-600 hover:text-blue-700 hover:underline md:inline-flex"
                            >
                                <Download className="h-4 w-4" />
                                匯出 JSON
                            </a>
                        </div>
                        <ul className="divide-y divide-gray-200">
                            {submissions.map((sub, idx) => (
                                <li key={idx} className="p-6 transition hover:bg-gray-50">
                                    <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4 border-l-4 border-blue-500 pl-4 bg-white/50">
                                        <div className="flex-1 space-y-3">
                                            <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-6">
                                                <div className="flex items-center gap-2 text-gray-900 font-semibold text-lg">
                                                    <User className="h-5 w-5 text-blue-500" />
                                                    {sub.name}
                                                </div>
                                                <div className="flex items-center gap-2 text-gray-600">
                                                    <Mail className="h-4 w-4 text-gray-400" />
                                                    <a href={`mailto:${sub.email}`} className="hover:text-blue-600 hover:underline">
                                                        {sub.email}
                                                    </a>
                                                </div>
                                            </div>
                                            
                                            <div className="rounded-lg bg-gray-50 p-4 border border-gray-100 mt-2">
                                                <h4 className="font-medium text-gray-900 flex items-center gap-2 mb-2">
                                                    主題：{sub.subject}
                                                </h4>
                                                <p className="text-gray-700 whitespace-pre-wrap text-sm leading-relaxed">
                                                    {sub.message}
                                                </p>
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-1.5 text-xs text-gray-500 shrink-0 md:pt-1 bg-gray-100 px-3 py-1.5 rounded-full">
                                            <Clock className="h-3.5 w-3.5" />
                                            <time dateTime={sub.submittedAt}>
                                                {new Date(sub.submittedAt).toLocaleString('zh-TW')}
                                            </time>
                                        </div>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    </div>
                )}
            </div>
        </div>
    );
}
