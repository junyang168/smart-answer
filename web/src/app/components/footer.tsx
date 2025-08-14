import Link from 'next/link';
import { Facebook, Youtube } from 'lucide-react';

export const Footer = () => {
  return (
    <footer className="bg-gray-800 text-gray-300">
      <div className="container mx-auto px-6 py-12">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          {/* Column 1: Church Info */}
          <div>
            <h3 className="text-lg font-bold text-white mb-4">達拉斯聖道教會</h3>
            <p className="mb-2">903 W. Parker Road, Plano, TX 75023</p>
            <p className="mb-2">
              <a href="mailto:ContactUs@Dallas-HLC.org" className="hover:text-[#D4AF37]">
                ContactUs@Dallas-HLC.org
              </a>
            </p>
            <p>主日崇拜: 週日上午 11:00</p>
          </div>

          {/* Column 2: Quick Links */}
          <div>
            <h3 className="text-lg font-bold text-white mb-4">快速連結</h3>
            <ul className="space-y-2">
              <li><Link href="/about" className="hover:text-[#D4AF37]">關於我們</Link></li>
              <li><Link href="/ministries" className="hover:text-[#D4AF37]">事工介紹</Link></li>
              <li><Link href="/events" className="hover:text-[#D4AF37]">聚會時間</Link></li>
              <li><Link href="/giving" className="hover:text-[#D4AF37]">奉獻支持</Link></li>
            </ul>
          </div>

          {/* Column 3: Social Media */}
          <div>
            <h3 className="text-lg font-bold text-white mb-4">關注我們</h3>
            <div className="flex space-x-4">
              {/* 
                ✅ 關鍵改動：為 <a> 標籤添加了初始顏色 text-gray-300
                這會被內部的 <Facebook /> SVG 組件繼承
              */}
              <a 
                href="https://www.facebook.com" 
                target="_blank" 
                rel="noopener noreferrer" 
                aria-label="Facebook" 
                className="text-gray-300 hover:text-white transition-colors"
              >
                <Facebook size={24} />
              </a>
              <a 
                href="https://www.youtube.com" 
                target="_blank" 
                rel="noopener noreferrer" 
                aria-label="YouTube" 
                className="text-gray-300 hover:text-white transition-colors"
              >
                <Youtube size={24} />
              </a>
            </div>
          </div>        </div>
      </div>
      <div className="bg-gray-900 py-4">
        <div className="container mx-auto px-6 text-center text-gray-500 text-sm">
          © {new Date().getFullYear()} Dallas Holy Logos Church. All Rights Reserved.
        </div>
      </div>
    </footer>
  );
};
