import { networkInterfaces } from 'node:os';

// Development only: Next blocks its dev scripts for pages opened from any host
// but localhost, so a phone on the home network gets HTML that never loads its
// data. Allow this machine's own private IPv4 addresses.
const lanAddresses = () =>
  Object.values(networkInterfaces())
    .flat()
    .filter((item) => item && item.family === 'IPv4' && !item.internal)
    .map((item) => item.address);

export default (phase, { defaultConfig }) => {
  const env = process.env.NODE_ENV;
  /**
   * @type {import("next").NextConfig}
   */
  return {
    allowedDevOrigins: env === 'production' ? [] : lanAddresses(),
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
      // Development only: sermon audio and the AI 朗讀 mp3s live under /web/video,
      // which production nginx serves; locally the same nginx answers on 8888.
      const devMedia = isProd
        ? []
        : [{ source: '/web/video/:path*', destination: `${process.env.DEV_MEDIA_ORIGIN ?? 'http://127.0.0.1:8888'}/web/video/:path*` }];
      return [
        ...devMedia,
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
          source: '/api/admin/sunday-assistant/:path*',
          destination: '/api/admin/sunday-assistant/:path*',
        },
        {
          source: '/api/admin/sunday-presenter/:path*',
          destination: '/api/admin/sunday-presenter/:path*',
        },
        {
          source: '/api/fellowship-documents/:path*',
          destination: '/api/fellowship-documents/:path*',
        },
        {
          source: '/api/:path((?!auth|sc_api).*)',
          destination,
        },
      ];
    },
  };
};
