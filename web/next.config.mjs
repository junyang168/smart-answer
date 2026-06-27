export default (phase, { defaultConfig }) => {
  const env = process.env.NODE_ENV;
  /**
   * @type {import("next").NextConfig}
   */
  return {
    experimental: {
      proxyTimeout: 120000,
    },
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
      const backendOrigin = isProd ? 'http://127.0.0.1:8555' : 'http://127.0.0.1:8222';
      const destination = `${backendOrigin}/:path*`;
      return [
        {
          source: '/api/auth/:path*',
          destination: '/api/auth/:path*',
        },
        {
          source: '/sc_api/:path*',
          destination: `${backendOrigin}/sc_api/:path*`,
        },
        {
          source: '/api/admin/fellowships/:path*',
          destination: '/api/admin/fellowships/:path*',
        },
        {
          source: '/api/:path((?!auth).*)',
          destination,
        },
      ];
    },
  };
};
