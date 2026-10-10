"use client";

import { Check, OctagonX, Play, Power, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, SectionHeader, Skeleton } from "@/components/ui/feedback";
import { Field, Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/misc";
import { StatTile } from "@/components/ui/stat";
import { eur, pct, timeAgo } from "@/lib/format";
import {
  useAudit,
  useAutonomy,
  useAutonomyActions,
  useAutonomyCommand,
  useDryRunReport,
  useEnableAutonomy,
  useResolveAction,
  type AutonomyAction,
  type AutonomyState,
} from "@/lib/ops";

const STATUS: Record<AutonomyAction["status"], { label: string; tone: "neutral" | "accent" | "success" | "warning" | "danger" }> = {
  dry_run: { label: "Simulated", tone: "neutral" },
  blocked: { label: "Blocked", tone: "warning" },
  pending_user: { label: "Waiting for you", tone: "accent" },
  done: { label: "Done", tone: "success" },
  rejected: { label: "Rejected", tone: "neutral" },
  failed: { label: "Failed", tone: "danger" },
};

const MODE_COPY: Record<AutonomyState["mode"], { title: string; text: string }> = {
  off: { title: "Off", text: "FlipFinder only suggests. Nothing is prepared on your behalf." },
  dry_run: { title: "Dry run", text: "It decides as if it were allowed to act, but only records what it would have done. Nothing is asked of you." },
  assisted: { title: "Assisted", text: "It prepares the action and waits. You perform it on Vinted yourself, then tell it what happened." },
};

function Limits() {
  const enable = useEnableAutonomy();
  const [f, setF] = useState({ daily_budget: "", weekly_budget: "", max_per_item: "", max_items: "", min_flip: "70", min_confidence: "60", max_risk: "40" });
  const [skip, setSkip] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const limits: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(f)) if (v.trim() !== "") limits[k] = Number(v);
    enable.mutate({ limits, skip_dry_run: skip });
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      <p className="text-[13px] text-fg-2">Without a budget it never prepares a purchase. Every limit is checked on each action and every violation is listed, not just the first.</p>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Field label="Daily budget €"><Input inputMode="decimal" value={f.daily_budget} onChange={set("daily_budget")} /></Field>
        <Field label="Weekly budget €"><Input inputMode="decimal" value={f.weekly_budget} onChange={set("weekly_budget")} /></Field>
        <Field label="Max per item €"><Input inputMode="decimal" value={f.max_per_item} onChange={set("max_per_item")} /></Field>
        <Field label="Max items"><Input inputMode="numeric" value={f.max_items} onChange={set("max_items")} /></Field>
        <Field label="Min flip score"><Input inputMode="numeric" value={f.min_flip} onChange={set("min_flip")} /></Field>
        <Field label="Min confidence"><Input inputMode="numeric" value={f.min_confidence} onChange={set("min_confidence")} /></Field>
        <Field label="Max risk"><Input inputMode="numeric" value={f.max_risk} onChange={set("max_risk")} /></Field>
      </div>
      <label className="flex items-center gap-2 text-[13px]">
        <Switch checked={skip} onCheckedChange={setSkip} aria-label="Skip the dry run" />
        <span>Skip the 7-day dry run <span className="text-fg-3">(not recommended: you lose the chance to see what it would have done)</span></span>
      </label>
      <Button type="submit" loading={enable.isPending}><Power /> Save limits and start</Button>
    </form>
  );
}

function ActionRow({ a }: { a: AutonomyAction }) {
  const resolve = useResolveAction();
  const st = STATUS[a.status];
  const title = (a.payload.title as string | undefined) ?? a.kind.replace(/_/g, " ");
  // What the agent or the cycle prepared; a markdown carries the price to set (the plan's, never a model's).
  const money = (v: unknown) => (v === undefined || v === null || Number.isNaN(Number(v)) ? null : Number(v));
  const price = money(a.payload.price);
  const was = money(a.payload.from_price);
  const todo = typeof a.payload.todo === "string" ? a.payload.todo : null;
  const why = typeof a.payload.reason === "string" ? a.payload.reason : null;
  return (
    <li className="rounded-xl border border-line p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={st.tone}>{st.label}</Badge>
        {a.payload.source === "agent" && <Badge tone="neutral">Agent</Badge>}
        {a.kind === "reprice" && <Badge tone="neutral">Markdown</Badge>}
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{title}</span>
        <span className="text-xs text-fg-3">{timeAgo(a.created_at)}</span>
      </div>
      {a.kind === "reprice" && price !== null && (
        <p className="mt-1.5 text-[13px] text-fg-2">
          Lower the price{was !== null && <> from {eur(was)}</>} to <strong>{eur(price)}</strong>
        </p>
      )}
      {why && <p className="mt-1.5 text-[13px] text-fg-2">Why: {why}</p>}
      {a.status === "pending_user" && todo && <p className="mt-1.5 text-[13px] text-fg-2">{todo}</p>}
      {a.reasons.length > 0 && (
        <ul className="mt-1.5 list-disc pl-5 text-[13px] text-fg-2">{a.reasons.map((r) => <li key={r.code}>{r.label}</li>)}</ul>
      )}
      {a.verifier && !a.verifier.agrees && (
        <p className="mt-1.5 rounded-lg bg-warning-soft px-2.5 py-1.5 text-[13px] text-warning">
          Independent check disagrees: {a.verifier.issues.map((i) => i.label).join("; ")}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {a.opportunity_id && <Link href={`/deals/${a.opportunity_id}`} className="text-[13px] text-accent underline-offset-2 hover:underline">Open the deal</Link>}
        {a.status === "pending_user" && (
          <span className="ml-auto flex gap-2">
            <Button size="sm" variant="outline" loading={resolve.isPending} onClick={() => resolve.mutate({ id: a.id, outcome: "rejected" })}><X /> I did not do it</Button>
            <Button size="sm" loading={resolve.isPending} onClick={() => resolve.mutate({ id: a.id, outcome: "done" })}><Check /> I did it on Vinted</Button>
          </span>
        )}
      </div>
    </li>
  );
}

export default function AutonomyPage() {
  const state = useAutonomy();
  const actions = useAutonomyActions();
  const report = useDryRunReport();
  const audit = useAudit();
  const kill = useAutonomyCommand("kill");
  const resume = useAutonomyCommand("resume");
  const disable = useAutonomyCommand("disable");
  const run = useAutonomyCommand("run");
  const s = state.data;
  const mode = s ? MODE_COPY[s.mode] : null;

  return (
    <div className="space-y-5">
      <SectionHeader title="Autonomy" description="How much FlipFinder may prepare on its own. Never an automatic click on Vinted: you perform every action." icon={<ShieldCheck />} />

      {state.isLoading || !s ? (
        <Skeleton className="h-32" />
      ) : (
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">{mode?.title} {s.killed && <Badge tone="danger">Stopped</Badge>} {s.suspended && <Badge tone="warning">Suspended</Badge>}</CardTitle>
              <CardDescription>{mode?.text}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {s.suspended && s.suspended_reason && <p className="rounded-xl bg-warning-soft px-3 py-2 text-[13px] text-warning">Suspended automatically: {s.suspended_reason}</p>}
            {s.dry_run && s.dry_run_until && <p className="text-[13px] text-fg-2">Dry run until {new Date(s.dry_run_until).toLocaleDateString()}. After that, actions are prepared for you to perform.</p>}
            <div className="flex flex-wrap gap-2">
              <Button variant="danger" onClick={() => kill.mutate()} loading={kill.isPending} disabled={s.killed || !s.enabled}><OctagonX /> Stop everything now</Button>
              {s.killed && <Button variant="outline" onClick={() => resume.mutate()} loading={resume.isPending}>Resume</Button>}
              {s.suspended && !s.killed && <Button variant="outline" onClick={() => resume.mutate()} loading={resume.isPending}>Lift suspension</Button>}
              <Button variant="outline" onClick={() => run.mutate()} loading={run.isPending} disabled={!s.enabled || s.killed || s.suspended}><Play /> Run a cycle now</Button>
              {s.enabled && <Button variant="ghost" onClick={() => disable.mutate()} loading={disable.isPending}>Turn off</Button>}
            </div>
            <p className="text-xs text-fg-3">Channels: {Object.entries(s.channels).map(([k, v]) => `${k} — ${v}`).join(" · ")}</p>
          </CardContent>
        </Card>
      )}

      {s && !s.enabled && (
        <Card>
          <CardHeader><div><CardTitle>Set the limits</CardTitle><CardDescription>Nothing is allowed that these do not allow.</CardDescription></div></CardHeader>
          <CardContent><Limits /></CardContent>
        </Card>
      )}

      {s?.enabled && (
        <Card>
          <CardHeader><div><CardTitle>Limits in force</CardTitle></div></CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[13px] sm:grid-cols-4">
              {Object.entries(s.limits).filter(([, v]) => v !== null && typeof v !== "object").map(([k, v]) => (
                <div key={k}><dt className="text-fg-3">{k.replace(/_/g, " ")}</dt><dd className="font-medium tnum">{String(v)}</dd></div>
              ))}
            </dl>
          </CardContent>
        </Card>
      )}

      {report.data && s?.enabled && (
        <Card>
          <CardHeader><div><CardTitle>Dry-run report</CardTitle><CardDescription>What it would have done, checked against what happened to those listings.</CardDescription></div></CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatTile label="Actions considered" value={report.data.actions} />
              <StatTile label="Would have bought" value={report.data.would_have_bought} sub={`${report.data.still_available} still available`} />
              <StatTile label="Gone since" value={report.data.gone_since} />
              <StatTile label="Independent check disagreed" value={report.data.verifier_disagreement_rate === null ? "—" : pct(report.data.verifier_disagreement_rate)} />
            </div>
            <p className="text-[13px] text-fg-2">{report.data.note}</p>
            {Object.keys(report.data.blocked_reasons).length > 0 && (
              <ul className="flex flex-wrap gap-1.5">{Object.entries(report.data.blocked_reasons).map(([k, n]) => <Badge key={k} tone="warning">{k.replace(/_/g, " ")} · {n}</Badge>)}</ul>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><div><CardTitle>Actions</CardTitle><CardDescription>Each one was checked by a second, independent verifier that can only lower or block.</CardDescription></div></CardHeader>
        <CardContent>
          {actions.isLoading ? <Skeleton className="h-24" /> : (actions.data ?? []).length === 0 ? (
            <EmptyState icon={<ShieldCheck />} title="No actions yet" description="Once it is on, what it considers appears here, including what it refused and why." />
          ) : (
            <ul className="space-y-2">{(actions.data ?? []).map((a) => <ActionRow key={a.id} a={a} />)}</ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><div><CardTitle>Audit trail</CardTitle><CardDescription>Append-only: every decision, who made it and why.</CardDescription></div></CardHeader>
        <CardContent>
          {(audit.data ?? []).length === 0 ? <p className="text-[13px] text-fg-3">Nothing recorded yet.</p> : (
            <ul className="divide-y divide-line text-[13px]">
              {(audit.data ?? []).map((e, i) => (
                <li key={`${e.at}-${i}`} className="flex flex-wrap gap-x-3 py-1.5">
                  <span className="w-24 shrink-0 text-fg-3">{timeAgo(e.at)}</span>
                  <span className="font-medium">{e.kind.replace(/_/g, " ")}</span>
                  <span className="text-fg-3">{e.actor}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
