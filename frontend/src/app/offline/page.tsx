import { Logo } from "@/components/layout/brand";

export const metadata = { title: "Offline" };

export default function OfflinePage() {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6 text-center">
      <Logo />
      <h1 className="text-lg font-semibold">You are offline</h1>
      <p className="max-w-sm text-sm text-fg-3">FlipFinder needs a connection to scan the market. Your data is safe: reconnect and the dashboard will refresh automatically.</p>
    </div>
  );
}
