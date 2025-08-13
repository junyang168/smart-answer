// components/contact/ContactForm.tsx
"use client";

import { useState } from 'react';
import { useForm, SubmitHandler } from 'react-hook-form';

type FormInputs = {
    name: string;
    email: string;
    subject: string;
    message: string;
};

export const ContactForm = () => {
    const { register, handleSubmit, formState: { errors, isSubmitting }, reset } = useForm<FormInputs>();
    const [formStatus, setFormStatus] = useState<{ status: 'idle' | 'success' | 'error'; message: string }>({ status: 'idle', message: '' });

    const onSubmit: SubmitHandler<FormInputs> = async (data) => {
        setFormStatus({ status: 'idle', message: '' }); // 重置狀態
        
        try {
            const response = await fetch('/api/contact', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(data),
            });

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.message || '發送失敗，請檢查您的輸入。');
            }

            setFormStatus({ status: 'success', message: result.message || '感謝您的留言！我們會盡快與您聯繫。' });
            reset(); // 清空表單

        } catch (error: any) {
            setFormStatus({ status: 'error', message: error.message || '發送失敗，請稍後重試或直接發送郵件。' });
        }
    };

    if (formStatus.status === 'success') {
        return (
            <div className="bg-green-50 border-l-4 border-green-500 text-green-700 p-4 rounded-md">
                <p className="font-bold">發送成功！</p>
                <p>{formStatus.message}</p>
            </div>
        );
    }

    return (
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
            <div>
                <label htmlFor="name" className="block text-sm font-medium text-gray-700">您的姓名</label>
                <input type="text" id="name" {...register('name', { required: '請輸入您的姓名' })} className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500" />
                {errors.name && <p className="mt-1 text-sm text-red-500">{errors.name.message}</p>}
            </div>
            <div>
                <label htmlFor="email" className="block text-sm font-medium text-gray-700">您的郵箱</label>
                <input type="email" id="email" {...register('email', { required: '請輸入您的郵箱地址', pattern: { value: /^\S+@\S+$/i, message: '郵箱格式不正確' } })} className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500" />
                {errors.email && <p className="mt-1 text-sm text-red-500">{errors.email.message}</p>}
            </div>
            <div>
                <label htmlFor="subject" className="block text-sm font-medium text-gray-700">主題</label>
                <input type="text" id="subject" {...register('subject', { required: '請輸入主題' })} className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500" />
                {errors.subject && <p className="mt-1 text-sm text-red-500">{errors.subject.message}</p>}
            </div>
            <div>
                <label htmlFor="message" className="block text-sm font-medium text-gray-700">信息內容</label>
                <textarea id="message" rows={5} {...register('message', { required: '請輸入您的信息' })} className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"></textarea>
                {errors.message && <p className="mt-1 text-sm text-red-500">{errors.message.message}</p>}
            </div>
            {formStatus.status === 'error' && <p className="text-red-500">{formStatus.message}</p>}
            <div>
                <button type="submit" disabled={isSubmitting} className="w-full flex justify-center py-3 px-4 border border-transparent rounded-md shadow-sm text-base font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:bg-gray-400">
                    {isSubmitting ? '發送中...' : '發送信息'}
                </button>
            </div>
        </form>
    );
};