// app/contact/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import Image from 'next/image';
import Link from 'next/link';
import { MapPin, Clock, Mail, Phone, Users } from 'lucide-react';
import { ContactForm } from '@/app/components/contact/ContactForm'; // 我們將創建這個客戶端組件

// 聯繫信息數據
const contactInfo = {
    address: "903 W. Parker Road, Plano, TX 75023",
    googleMapsUrl: "https://www.google.com/maps/place/903+W+Parker+Rd,+Plano,+TX+75023",
    worshipTime: "每週日上午 11:00 - 12:30",
    email: "ContactUs@Dallas-HLC.org",
    phone: "972-123-4567" // 假設的電話
};

export default function ContactPage() {
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: '聯繫我們' },
  ];

  return (
    <div className="bg-gray-50">
      <div className="container mx-auto px-6 py-12">
        <Breadcrumb links={breadcrumbLinks} />
        
        {/* 1. 頁面標題與歡迎語 */}
        <div className="text-center mb-12 md:mb-16">
          <h1 className="text-4xl md:text-5xl font-bold font-display text-gray-800">與我們聯繫</h1>
          <p className="mt-4 text-lg text-gray-600 max-w-2xl mx-auto">
            我們非常樂意聽到您的聲音。無論您是想了解更多關於我們教會的信息，還是需要代禱和幫助，請隨時通過以下方式聯繫我們。
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 lg:gap-16">
          {/* 左側：核心信息和地圖 */}
          <div className="space-y-8">
            {/* 2. 核心聯繫信息區 */}
            <div>
                <h2 className="text-2xl font-bold mb-4">聚會信息與聯繫方式</h2>
                <div className="space-y-4 text-gray-700">
                    <div className="flex items-start gap-4">
                        <MapPin className="w-6 h-6 text-blue-600 mt-1 flex-shrink-0"/>
                        <div>
                            <p className="font-semibold">{contactInfo.address}</p>
                            <a href={contactInfo.googleMapsUrl} target="_blank" rel="noopener noreferrer" className="text-sm text-blue-600 hover:underline">在 Google 地圖上查看</a>
                        </div>
                    </div>
                    <div className="flex items-start gap-4">
                        <Clock className="w-6 h-6 text-blue-600 mt-1 flex-shrink-0"/>
                        <p><span className="font-semibold">主日崇拜:</span> {contactInfo.worshipTime}</p>
                    </div>
                    <div className="flex items-start gap-4">
                        <Mail className="w-6 h-6 text-blue-600 mt-1 flex-shrink-0"/>
                        <a href={`mailto:${contactInfo.email}`} className="hover:underline">{contactInfo.email}</a>
                    </div>
                </div>
            </div>

            {/* Google 地圖嵌入 */}
            <div className="aspect-w-16 aspect-h-9 rounded-lg overflow-hidden shadow-md">
                <iframe
                    src="https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d3345.923149098313!2d-96.7268886848119!3d33.00642298090388!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x864c172a6b7d5f3d%3A0x6b7d5f3d1b7d5f3d!2s903%20W%20Parker%20Rd%2C%20Plano%2C%20TX%2075023%2C%20USA!5e0!3m2!1sen!2sca!4v1628526888624!5m2!1sen!2sca"
                    width="100%"
                    height="100%"
                    style={{ border: 0 }}
                    allowFullScreen={true}
                    loading="lazy"
                ></iframe>
            </div>
          </div>

          {/* 右側：聯繫表單 */}
          <div className="bg-white p-8 rounded-lg shadow-lg">
            <h2 className="text-2xl font-bold mb-6">發送信息給我們</h2>
            {/* 3. 聯繫表單 (客戶端組件) */}
            <ContactForm />
          </div>
        </div>
      </div>
    </div>
  );
}