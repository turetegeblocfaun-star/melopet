import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Playlist Pet · 歌单生成 AI 桌宠',
  description: '把你的歌单，变成一只专属 AI 桌宠。',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
