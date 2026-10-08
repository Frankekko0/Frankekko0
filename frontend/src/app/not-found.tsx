import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-[60dvh] flex-col items-center justify-center gap-3 px-6 text-center">
      <p className="text-sm font-semibold text-accent">404</p>
      <h1 className="text-xl font-semibold tracking-tight">Page not found</h1>
      <p className="text-sm text-fg-3">The page you are looking for does not exist or the deal is no longer available.</p>
      <Link href="/" className="mt-2 text-sm font-medium text-accent hover:underline">
        Back to dashboard
      </Link>
    </main>
  );
}
