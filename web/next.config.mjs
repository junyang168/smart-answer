export default (phase, { defaultConfig }) => {
  const env = process.env.NODE_ENV;
  /**
   * @type {import("next").NextConfig}
   */
  return {
    turbopack: {
      root: process.cwd(),
    },
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
      // Development only: a git worktree runs its own backend on its own port,
      // and this rewrite -- which takes precedence over the app's own route
      // handlers -- would otherwise send every /api call to whichever checkout
      // happens to hold 8222. Production is left hardcoded on purpose.
      const devBackendOrigin = process.env.DEV_BACKEND_ORIGIN ?? 'http://127.0.0.1:8222';
      const backendOrigin = isProd ? 'http://127.0.0.1:8555' : devBackendOrigin;
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
          source: '/api/fellowship-documents/:path*',
          destination: '/api/fellowship-documents/:path*',
        },
        {
          source: '/api/:path((?!auth).*)',
          destination,
        },
      ];
    },
  };
};
