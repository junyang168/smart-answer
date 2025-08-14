// components/admin/qa/QAEditorBrowser.tsx
"use client";

import { useState, useEffect } from 'react';
import { FaithQA } from '@/app/interfaces/article';
import { QAListPanel } from '@/app/components/admin/qa/QAListPanel';
import { QAEditPanel } from '@/app/components/admin/qa/QAEditPanel';
import { useSession, signIn } from "next-auth/react"; // ✅ 引入 useSession 和 signIn
import { Lock } from 'lucide-react';

// 模擬 API 獲取函數
async function fetchAllQAs(): Promise<FaithQA[]> {
    const user_id = 'junyang168@gmail.com'
    const res = await fetch(`/sc_api/qas/${user_id}`);
    const data = await res.json();
    return data;
}


export const QAEditorBrowser = () => {
    const [qas, setQas] = useState<FaithQA[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false); // ✅ 新增：保存中的狀態
    const [isDeleting, setIsDeleting] = useState(false); // ✅ 新增：刪除中的狀態
    const [error, setError] = useState<string | null>(null); // ✅ 新增：用於顯示錯誤信息

    const { data: session, status } = useSession(); // ✅ 獲取 session 狀態
   
    useEffect(() => {
        fetchAllQAs().then(data => {
            setQas(data);
            setIsLoading(false);
            if (data.length > 0) {
                setSelectedId(data[0].id); // 默認選中第一項
            }
        });
    }, []);

 // ✅ 重寫 handleSave 函數以包含 API 調用
    const handleSave = async (dataToSave: FaithQA) => {
        setIsSaving(true);
        setError(null);

        const isNew = dataToSave.id.startsWith('new-');
        const user_id = 'junyang168@gmail.com'
        const endpoint = `/sc_api/qas/${user_id}` ;
        const method = isNew ? 'POST' : 'PUT';

        const payload = dataToSave

        try {
            const response = await fetch(endpoint, {
                method: method,
                headers: {
                    'Content-Type': 'application/json',
                    // 如果需要授權，在這裡添加 Authorization header
                    // 'Authorization': `Bearer ${your_auth_token}`
                },
                body: JSON.stringify(payload),
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || '保存失敗，請稍後重試。');
            }

            const savedQA: FaithQA = await response.json(); // API 應返回已保存的對象（包含由數據庫生成的ID）

            // 更新本地 state
            setQas(prevQas => {
                if (isNew) {
                    // 將帶有臨時ID的條目替換為從服務器返回的、帶有真實ID的條目
                    return [savedQA, ...prevQas.filter(q => q.id !== dataToSave.id)];
                } else {
                    // 更新現有條目
                    return prevQas.map(q => q.id === savedQA.id ? savedQA : q);
                }
            });

            // 如果是新增，更新選中的 ID 為服務器返回的真實 ID
            if (isNew) {
                setSelectedId(savedQA.id);

            }
            
            // 可以在這裡添加一個成功提示，例如使用一個 toast 通知庫

        } catch (err: any) {
            setError(err.message);
        } finally {
            setIsSaving(false);
        }
    };
    
    
const handleDelete = async (idToDelete: string) => {
        // 如果條目是還未保存的新條目，直接從 state 中移除即可，無需 API 調用
        if (idToDelete.startsWith('new-')) {
            setQas(prevQas => prevQas.filter(q => q.id !== idToDelete));
            // 選擇列表中的下一項或清空
            const remainingQas = qas.filter(q => q.id !== idToDelete);
            setSelectedId(remainingQas.length > 0 ? remainingQas[0].id : null);
            return;
        }

        setIsDeleting(true);
        setError(null);
        const user_id = 'junyang168@gmail.com';
        const endpoint = `/sc_api/qas/${user_id}/${idToDelete}`;

        try {
            const response = await fetch(endpoint, {
                method: 'DELETE',
                headers: {
                    // 如果需要授權，在這裡添加 Authorization header
                    // 'Authorization': `Bearer ${your_auth_token}`
                },
            });

            // DELETE 請求成功時通常返回 200, 202, 或 204
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || '刪除失敗，請稍後重試。');
            }

            // API 成功後，更新本地 state
            let nextSelectedId: string | null = null;
            setQas(prevQas => {
                const index = prevQas.findIndex(q => q.id === idToDelete);
                const remainingQas = prevQas.filter(q => q.id !== idToDelete);
                
                // 決定下一個被選中的 ID
                if (remainingQas.length > 0) {
                    // 如果被刪除的不是最後一項，則選中原來位置的下一項；否則選中新的最後一項
                    nextSelectedId = remainingQas[Math.min(index, remainingQas.length - 1)].id;
                }
                
                return remainingQas;
            });

            // 更新選中的 ID
            setSelectedId(nextSelectedId);

        } catch (err: any) {
            setError(err.message);
        } finally {
            setIsDeleting(false);
        }
    };

    const handleAddNew = () => {
        const newId = `new-${Date.now()}`; // 使用時間戳生成臨時 ID
        const today = new Date();
        const date_asked = today.toISOString().split('T')[0]; // 'yyyy-mm-dd'
        const newQA: FaithQA = {
            id: newId,
            question: '新的問題標題',
            shortAnswer: '',
            fullAnswerMarkdown: '',
            category: '未分類',
            relatedScriptures: [],
            createdAt: today.toISOString(),
            isVerified: false,
            related_article: '',
            date_asked : today.toISOString().split('T')[0], // 'yyyy-mm-dd' 
        };
        setQas([newQA, ...qas]);
        setSelectedId(newId);
    };

    const selectedQA = qas.find(q => q.id === selectedId);

    if (status === "unauthenticated") {
        // 如果用戶未登錄，顯示一個登錄提示界面
        return (
        <div className="text-center py-20 bg-gray-50 rounded-lg max-w-lg mx-auto">
            <Lock className="w-12 h-12 mx-auto text-gray-400 mb-4" />
            <h2 className="text-2xl font-bold mb-2">需要登錄</h2>
            <p className="text-gray-600 mb-6">此內容僅對已登錄用戶開放，請先登錄以繼續訪問。</p>
            <button
            onClick={() => signIn("google")}
            className="bg-blue-500 text-white font-semibold py-3 px-6 rounded-full hover:bg-blue-600 text-lg"
            >
            使用 Google 登錄
            </button>
        </div>
        );
    }


    if (isLoading) return <div className="p-8">正在加載...</div>;

    return (
        <div className="flex h-screen">
            {/* 左側列表欄 */}
            <div className="w-1/3 border-r bg-white h-full overflow-y-auto">
                <QAListPanel 
                    qas={qas} 
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                    onAddNew={handleAddNew}
                />
            </div>

            {/* 右側編輯區 */}
            <div className="w-2/3 h-full overflow-y-auto p-8">
                {selectedQA ? (
                    <QAEditPanel 
                        key={selectedQA.id} // 關鍵！確保在切換 QA 時重新渲染表單
                        qa={selectedQA} 
                        onSave={handleSave}
                        onDelete={handleDelete}
                        isSaving={isSaving}
                    />
                ) : (
                    <div className="text-center pt-20">
                        <p>請從左側選擇一個問答進行編輯，或新增一個問答。</p>
                    </div>
                )}
            </div>
        </div>
    );
};