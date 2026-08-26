/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Allow the frontend container to call the backend container directly.
  experimental: {
    serverActions: { allowedOrigins: ["localhost:3000", "backend:8000"] },
  },
};

export default nextConfig;