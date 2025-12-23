export default (phase, { defaultConfig }) => {
  const env = process.env.NODE_ENV;
  /**
   * @type {import("next").NextConfig}
   */
  return {
    images: {
      remotePatterns: [
        {
          protocol: 'https',
          hostname: '*.googleusercontent.com'
        },
      ],
    },
    async rewrites() {
      const isProd = process.env.NODE_ENV === 'production';
      const destination = isProd ? 'http://127.0.0.1:8555/:path*' : 'http://127.0.0.1:8222/:path*';
      return [
        {
          source: '/api/auth/:path*',
          destination: '/api/auth/:path*',
        },
        {
          source: '/api/:path((?!auth).*)',
          destination,
        },
      ];
    },
  };
};
