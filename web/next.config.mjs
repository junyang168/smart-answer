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
        }      
                
      };
  }
  