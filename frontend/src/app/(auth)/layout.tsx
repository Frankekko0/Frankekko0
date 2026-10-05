import { BadgeCheck, Calculator, Radar } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
import { Logo } from "@/components/layout/brand";

// Relative bar heights of the illustration (a typical price distribution shape, no data).
const BARS = [18, 30, 46, 62, 84, 100, 88, 70, 52, 36, 24, 14];

const STEPS = [
  { icon: Radar, title: "Scans every new listing", text: "Fresh listings are analysed within seconds, so you see a deal before everyone else." },
  { icon: BadgeCheck, title: "Knows the real market price", text: "Comparable sold items, adjusted for condition, with outliers left out." },
  { icon: Calculator, title: "Profit after every cost", text: "Buyer protection, shipping, fees and your own costs, for a margin you can trust." },
];

function Showcase() {
  return (
    <div className="relative flex h-full flex-col justify-center overflow-hidden bg-[#0b0b0c] px-12 py-14 text-white xl:px-16">
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.5]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.06) 1px, transparent 1px)",
          backgroundSize: "44px 44px",
          maskImage: "radial-gradient(70% 60% at 60% 40%, #000, transparent)",
        }}
        aria-hidden
      />
      <div
        className="aurora opacity-90"
        style={{
          background:
            "radial-gradient(40% 55% at 70% 30%, rgba(255,106,61,0.28), transparent 70%), radial-gradient(40% 50% at 25% 60%, rgba(75,143,234,0.22), transparent 70%)",
        }}
        aria-hidden
      />

      <div className="relative max-w-lg">
        <p className="enter text-[12px] font-semibold uppercase tracking-[0.14em] text-white/50">Vinted deal intelligence</p>
        <h2 className="enter mt-3 text-[40px] font-semibold leading-[1.05] tracking-[-0.03em]" style={{ "--i": 1 } as CSSProperties}>
          Buy below market.
          <br />
          <span className="bg-gradient-to-r from-[#ff6a3d] to-[#fbbf24] bg-clip-text text-transparent">Sell with a real margin.</span>
        </h2>

        <figure
          className="enter mt-10 rounded-2xl border border-white/10 bg-white/[0.04] p-5 shadow-[0_30px_80px_-40px_rgba(0,0,0,0.9)] backdrop-blur-md"
          style={{ "--i": 2 } as CSSProperties}
          aria-hidden
        >
          <div className="flex items-end gap-1.5" style={{ height: 120 }}>
            {BARS.map((h, i) => (
              <span
                key={i}
                className="flex-1 origin-bottom rounded-t-[4px]"
                style={{
                  height: `${h}%`,
                  background: i === 1 ? "linear-gradient(#ff6a3d, #fbbf24)" : "rgba(75,143,234,0.75)",
                  animation: `grow-y 0.7s var(--ease-out) ${0.25 + i * 0.045}s backwards`,
                }}
              />
            ))}
          </div>
          <div className="mt-3 flex justify-between text-[12px] text-white/55">
            <span className="flex items-center gap-1.5">
              <span className="size-2 rounded-full bg-[#ff6a3d]" /> Listing price
            </span>
            <span className="flex items-center gap-1.5">
              <span className="size-2 rounded-full bg-[#4b8fea]" /> What it really sells for
            </span>
          </div>
        </figure>

        <ul className="mt-10 space-y-5">
          {STEPS.map((s, i) => (
            <li key={s.title} className="enter flex gap-3.5" style={{ "--i": 3 + i } as CSSProperties}>
              <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-white/[0.07] ring-1 ring-white/10">
                <s.icon className="size-4 text-white/85" />
              </span>
              <div>
                <p className="text-[14px] font-semibold">{s.title}</p>
                <p className="mt-0.5 text-[13px] leading-relaxed text-white/55">{s.text}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
      <div className="relative flex flex-col justify-center overflow-hidden px-4 py-10 sm:px-10">
        <div className="aurora" aria-hidden />
        <div className="relative mx-auto w-full max-w-sm">
          <div className="enter mb-10 flex justify-center lg:justify-start">
            <Logo className="origin-center scale-125 lg:origin-left" />
          </div>
          {children}
          <p className="mt-8 text-center text-[12px] leading-relaxed text-fg-3 lg:text-left">
            FlipFinder only analyses listings. Purchases and offers are always made by you, on the marketplace.
          </p>
        </div>
      </div>
      <aside className="hidden lg:block">
        <Showcase />
      </aside>
    </div>
  );
}
