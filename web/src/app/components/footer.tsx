import Link from 'next/link';

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
              <li><Link href="/events" className="hover:text-[#D4AF37]">聚會時間</Link></li>
              <li><Link href="/resources" className="hover:text-[#D4AF37]">資源中心</Link></li>
              <li><Link href="/giving" className="hover:text-[#D4AF37]">奉獻支持</Link></li>
            </ul>
          </div>

          {/* Column 3: Social Media */}
          <div>
            <h3 className="text-lg font-bold text-white mb-4">關注我們</h3>
            <div className="flex space-x-4">
              <a href="#" target="_blank" rel="noopener noreferrer" aria-label="Facebook" className="hover:text-[#D4AF37]">
              </a>
              <a href="#" target="_blank" rel="noopener noreferrer" aria-label="YouTube" className="hover:text-[#D4AF37]">
              </a>
            </div>
          </div>
        </div>
      </div>
      <div className="bg-gray-900 py-4">
        <div className="container mx-auto px-6 text-center text-gray-500 text-sm">
          © {new Date().getFullYear()} Dallas Holy Logos Church. All Rights Reserved.
        </div>
      </div>
    </footer>
  );
};
