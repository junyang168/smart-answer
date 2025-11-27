'use client';

import React, { useState, useMemo } from 'react';
import { useSession } from 'next-auth/react';
import dynamic from 'next/dynamic';
import 'react-quill/dist/quill.snow.css';

const ReactQuill = dynamic(() => import('react-quill'), { ssr: false });

export default function EmailSender() {
    const { data: session } = useSession();
    const [subject, setSubject] = useState('');
    const [body, setBody] = useState('');
    const [status, setStatus] = useState<'idle' | 'sending' | 'success' | 'error'>('idle');
    const [message, setMessage] = useState('');

    const modules = useMemo(() => ({
        toolbar: [
            [{ 'header': [1, 2, false] }],
            ['bold', 'italic', 'underline', 'strike', 'blockquote'],
            [{ 'list': 'ordered' }, { 'list': 'bullet' }, { 'indent': '-1' }, { 'indent': '+1' }],
            ['link', 'image'],
            ['clean']
        ],
    }), []);

    const formats = [
        'header',
        'bold', 'italic', 'underline', 'strike', 'blockquote',
        'list', 'bullet', 'indent',
        'link', 'image'
    ];

    const handleSend = async () => {
        if (!subject || !body) {
            setMessage('Please fill in both subject and body.');
            return;
        }

        if (!confirm('Are you sure you want to send this email to the congregation?')) {
            return;
        }

        setStatus('sending');
        setMessage('');

        try {
            const response = await fetch('/api/admin/email/send', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    subject,
                    body,
                    recipients_type: 'congregation',
                }),
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to send email');
            }

            const data = await response.json();
            setStatus('success');
            setMessage(`Email sent successfully to ${data.recipient_count} recipients.`);
            setSubject('');
            setBody('');
        } catch (error: any) {
            setStatus('error');
            setMessage(error.message);
        }
    };

    return (
        <div className="p-6 max-w-4xl mx-auto">
            <h1 className="text-2xl font-bold mb-6">Send Email to Congregation</h1>

            <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">Subject</label>
                <input
                    type="text"
                    className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    placeholder="Enter email subject"
                />
            </div>

            <div className="mb-6">
                <label className="block text-sm font-medium text-gray-700 mb-1">Body</label>
                <div className="bg-white">
                    <ReactQuill
                        theme="snow"
                        value={body}
                        onChange={setBody}
                        modules={modules}
                        formats={formats}
                        className="h-64 mb-12" // Add margin bottom to account for toolbar
                    />
                </div>
            </div>

            <div className="flex items-center justify-between mt-12">
                <button
                    onClick={handleSend}
                    disabled={status === 'sending'}
                    className={`px-4 py-2 rounded-md text-white font-medium ${status === 'sending'
                        ? 'bg-gray-400 cursor-not-allowed'
                        : 'bg-blue-600 hover:bg-blue-700'
                        }`}
                >
                    {status === 'sending' ? 'Sending...' : 'Send Email'}
                </button>

                {message && (
                    <div
                        className={`px-4 py-2 rounded-md ${status === 'success' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                            }`}
                    >
                        {message}
                    </div>
                )}
            </div>

            {/* Preview Section */}
            <div className="mt-8 border-t pt-6">
                <h2 className="text-lg font-semibold mb-4">Preview</h2>
                <div className="border border-gray-200 rounded-md p-4 min-h-[200px] bg-white prose max-w-none">
                    <div dangerouslySetInnerHTML={{ __html: body }} />
                </div>
            </div>
        </div>
    );
}
