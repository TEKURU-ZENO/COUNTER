/** @type {import('next').NextConfig} */
// The browser only talks to this origin. /api is proxied to the FastAPI service, so the
// merchant session cookie is first-party and the API's CORS can stay locked to this origin.
const rawApi = process.env.API_INTERNAL_URL || "http://localhost:8000";
// Render private networking gives host:port without a scheme.
const apiUrl = rawApi.startsWith("http://") || rawApi.startsWith("https://") ? rawApi : `http://${rawApi}`;

const nextConfig = {
  reactStrictMode: true,
  compress: false, // keep SSE responses unbuffered through the proxy
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiUrl.replace(/\/$/, "")}/api/:path*` },
      { source: "/healthz", destination: `${apiUrl.replace(/\/$/, "")}/healthz` },
    ];
  },
};

module.exports = nextConfig;
