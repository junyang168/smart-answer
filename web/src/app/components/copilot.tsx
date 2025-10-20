// components/CopilotChat.tsx
'use client';
import React, { FC, useEffect, useState, useRef } from "react";
import { authConfig} from "@/app/utils/auth";
import MarkdownView from 'react-showdown';
import { useSession } from 'next-auth/react';
import { highlightReferences } from "@/app/utils/funcs";

interface Reference {
  Id: string;
  Title: string;
  Index: string;
}

interface Message {
  content: string;
  references?: Reference[];
  role: 'user' | 'assistant';
}

export const CopilotChat: FC<{item_id:string }> = ({ item_id} ) => {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState<string>('');
  const chatFlyoverRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const { data: session, status } = useSession();

  let user_id: string | null | undefined = 'junyang168@gmail.com';
  if (session && session.user) {
    user_id =  session.user.email;      
  }


  // Handle clicks outside to close chat
  useEffect(() => {

    function handleClickOutside(event: MouseEvent) {
      if (
        chatFlyoverRef.current &&
        !chatFlyoverRef.current.contains(event.target as Node) &&
        !((event.target as HTMLElement).closest('#help-button'))
      ) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Auto-scroll to latest message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const sendMessage = async (msg:string) => {
    if(!msg.trim()) return;

    const userMessage: Message = { content: msg, role: 'user' };
    const thinkMessage: Message = { content: 'thinking...', role: 'assistant' };
    setMessages((prev) => [...prev, userMessage, thinkMessage]);
    const history = [...messages, userMessage];
    setInput('');


    
    // Example with actual API integration (uncomment and modify):
    try {
      const api_prefix = '/api/sc_api/'

      const url = `${api_prefix}chat/${user_id}`;  
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          item: item_id,
          history: history
        })
      });
      const data = await response.json();
      setMessages((prev) => prev.filter((msg) => msg.content !== 'thinking...'));
      setMessages((prev) => [...prev, { content: data.answer , references: data.quotes, role: 'assistant' }]);

    } catch (error) {
      setMessages((prev) => [...prev, { content: 'Error occurred', role: 'assistant' }]);
    }

  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };


  const handleButtonClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    const buttonText = e.currentTarget.innerText;
    sendMessage(buttonText);
  };

  const clearChat = () => {
    setMessages([]);
    setInput('');
  };



  return (
    <>
      <button
        id="help-button"
        className="fixed right-5 top-20 -translate-y-1/2 bg-blue-600 text-white px-5 py-2.5 rounded-md cursor-pointer z-[1000] hover:bg-blue-700 transition-colors"
        onClick={() => setIsOpen(true)}
      >
        Chat with AI
      </button>
      <div
        ref={chatFlyoverRef}
        className={`fixed top-0 w-[400px] h-full bg-white shadow-[-2px_0_5px_rgba(0,0,0,0.3)] transition-all duration-300 ease-in-out z-[1001] flex flex-col ${
          isOpen ? 'right-0' : 'right-[-400px]'
        }`}
      >
        <div className="bg-blue-600 text-white p-2.5 flex justify-between items-center">
          <h3 className="m-0">Chat with AI</h3>
          <button
            className="bg-transparent border-none text-white text-xl cursor-pointer hover:text-gray-200"
            onClick={() => setIsOpen(false)}
          >
            ×
          </button>
        </div>
        <div className="flex-1 p-2.5 overflow-y-auto" id='copilot-chat'>
          {messages.map((msg, index) => (
            <div
              key={index}
              className={`my-2.5 p-2 rounded ${
              msg.role === 'user'
                ? 'bg-blue-600 text-white ml-[20%]'
                : 'bg-gray-100 mr-[20%]'
              } ${ msg.content === 'thinking...' ? 'animate-pulse' : ''}
              `}
            >
              <MarkdownView markdown={msg.content } />
              {msg.references && (
                msg.references.map((ref, idx) => (
                  <p key={idx} className="text-sm text-gray-500">
                    {idx+1}.
                    <a 
                      href={`/${ref.Index}`} 
                      className="hover:underline" 
                      target="_blank" 
                      rel="noopener noreferrer" 
                      onClick={(e) => {
                        e.preventDefault();
                        highlightReferences(ref.Index);
                        }}
                      >
                      {ref.Title}
                    </a>
                  </p>
                ))
              
              )}

            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>
        <div className="p-2.5 border-t border-gray-200 flex justify-start">
            <button
            className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 transition-colors mr-2"
            onClick={(e: React.MouseEvent<HTMLButtonElement>) => handleButtonClick(e)}
            >
            總結主题
            </button>          
            <button
            className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 transition-colors"
            onClick={() => clearChat()}
            >
            清空對話
            </button>
        </div>
        <div className="p-2.5 border-t border-gray-200">
          <textarea
            className="w-full p-2 border border-gray-300 rounded resize-none focus:outline-none focus:ring-2 focus:ring-blue-500"
            value={input}
            onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Type your question..."
            rows={3}
          />
        </div>
      </div>
    </>
  );
}
