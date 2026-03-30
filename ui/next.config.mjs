import env from '@next/env';
import path from 'path';

env.loadEnvConfig(path.resolve(process.cwd(), '..'), undefined, undefined, true);

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  serverExternalPackages: ["@modelcontextprotocol/sdk"],
};

export default nextConfig;
