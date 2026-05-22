/** @type {import('next').NextConfig} */
const BACKEND = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      { source: '/api/:path*', destination: `${BACKEND}/api/:path*` },
    ]
  },
}

module.exports = nextConfig
