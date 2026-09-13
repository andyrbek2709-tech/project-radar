/** @type {import('next').NextConfig} */
const nextConfig = {
  // standalone нужен для тонкого Docker-образа на Railway
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
