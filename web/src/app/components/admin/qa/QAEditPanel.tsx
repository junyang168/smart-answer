// components/admin/qa/QAEditPanel.tsx
"use client";

import { useForm, Controller } from 'react-hook-form';
import { FaithQA } from '@/app/interfaces/article';
import dynamic from 'next/dynamic';
import "easymde/dist/easymde.min.css"; // 導入樣式

// 動態導入 Markdown 編輯器，因為它只在客戶端運行
const SimpleMDE = dynamic(() => import('react-simplemde-editor'), { ssr: false });

interface QAEditPanelProps {
    qa: FaithQA;
    onSave: (data: FaithQA) => void;
    onDelete: (id: string) => void;
    isSaving: boolean;
}

export const QAEditPanel = ({ qa, onSave, onDelete, isSaving }: QAEditPanelProps) => {
    const { register, handleSubmit, control, formState: { errors, isDirty } } = useForm<FaithQA>({
        defaultValues: qa
    });

    const onSubmit = (data: FaithQA) => {
        onSave(data);
    };

    const handleDeleteClick = () => {
        if (window.confirm(`您確定要刪除問題 "${qa.question}" 嗎？此操作無法撤銷。`)) {
            onDelete(qa.id);
        }
    }

    return (
        <form onSubmit={handleSubmit(onSubmit)}>
            {/* 各個輸入字段 */}
            <div className="space-y-6">
                <div>
                    <label htmlFor="question" className="block font-medium mb-1">問題</label>
                    <input id="question" {...register('question', { required: '問題不能為空' })} className="w-full p-2 border rounded-md" />
                    {errors.question && <p className="text-red-500 text-sm mt-1">{errors.question.message}</p>}
                </div>

                <div>
                    <label htmlFor="shortAnswer" className="block font-medium mb-1">簡短答案</label>
                    <textarea id="shortAnswer" {...register('shortAnswer')} rows={3} className="w-full p-2 border rounded-md" />
                </div>

                <div>
                    <label className="block font-medium mb-1">完整答案 (Markdown)</label>
                    <Controller
                        name="fullAnswerMarkdown"
                        control={control}
                        render={({ field }) => <SimpleMDE {...field} />}
                    />
                </div>
                
                {/* 分類、經文、狀態等其他字段可以按同樣的模式添加 */}
                <div className="grid grid-cols-2 gap-6">
                     <div>
                        <label htmlFor="category" className="block font-medium mb-1">分類</label>
                        <input id="category" {...register('category')} className="w-full p-2 border rounded-md" />
                    </div>
                     <div>
                        <label htmlFor="isVerified" className="block font-medium mb-1">狀態</label>
                        <select id="isVerified" {...register('isVerified')} className="w-full p-2 border rounded-md bg-white">
                            <option value="true">已審核發佈</option>
                            <option value="false">草稿/待審核</option>
                        </select>
                    </div>
                </div>

            </div>

            {/* 底部操作按鈕 */}
            <div className="mt-8 pt-6 border-t flex justify-between items-center">
                <button type="button" onClick={handleDeleteClick} className="text-red-600 hover:text-red-800 font-semibold">
                    刪除
                </button>
                <button 
                    type="submit" 
                    disabled={!isDirty || isSaving} 
                    className="bg-green-600 text-white font-semibold py-2 px-6 rounded-md hover:bg-green-700 disabled:bg-gray-300 disabled:cursor-not-allowed flex items-center"
                >
                    {isSaving && (
                        <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                        </svg>
                    )}
                    {isSaving ? '保存中...' : '保存更改'}
                </button>
            </div>
        </form>
    );
};