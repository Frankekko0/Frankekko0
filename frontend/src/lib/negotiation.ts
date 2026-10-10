// Labels and wording for the negotiation messages: which message is which, why the model's draft was or was not used.
import type { NegotiationAi } from "./ops";

/** The kinds of message the backend writes (templates, or the model's words around the code's numbers). */
export const MESSAGE_LABELS: Record<string, string> = {
  first_offer: "First offer",
  counter_reply: "If the seller counters",
  accept: "Accept",
  decline_politely: "Decline politely",
  bundle: "Bundle",
};

export function messageLabel(kind: string): string {
  return MESSAGE_LABELS[kind] ?? kind.replace(/_/g, " ");
}

export const TONES = [
  { value: "polite", label: "Polite" },
  { value: "direct", label: "Direct" },
  { value: "firm", label: "Firm" },
] as const;

/** Why the templates stayed (``ai.fallback`` of the negotiation answer). */
const FALLBACKS: Record<string, string> = {
  disabled: "AI writing is switched off (NEGOTIATION_AI_ENABLED).",
  no_model: "No AI model is configured (AI_API_KEY).",
  injection: "The listing title looks like it gives orders, so it was not sent to the model.",
  cooldown: "A draft was just written for this item. Wait a moment before asking again.",
  daily_cap: "The daily limit of AI drafts is reached.",
  rate_limited: "The AI model's request limit is reached.",
  unavailable: "The AI model is not answering right now.",
  no_answer: "The model gave no usable answer.",
  guardrail: "Every AI draft broke a safety rule, so the templates stay.",
};

export function retryText(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return "";
  if (seconds < 90) return `Try again in ${Math.ceil(seconds)} s.`;
  if (seconds < 5400) return `Try again in ${Math.ceil(seconds / 60)} min.`;
  return `Try again in ${Math.ceil(seconds / 3600)} h.`;
}

/** The sentence for a fallback, with when to retry; ``null`` when there is nothing to explain. */
export function fallbackText(ai: Pick<NegotiationAi, "fallback" | "retry_after"> | undefined): string | null {
  if (!ai?.fallback) return null;
  const base = FALLBACKS[ai.fallback] ?? "The AI draft was not used.";
  const retry = retryText(ai.retry_after);
  return retry ? `${base} ${retry}` : base;
}

/** What a rule broken by a draft means, for the line under a message that kept its template. */
const VIOLATIONS: Record<string, string> = {
  empty: "was empty",
  too_short: "was too short",
  too_long: "was too long",
  multi_paragraph: "had several paragraphs",
  bad_format: "used markup or emoji",
  literal_number: "wrote a number of its own",
  literal_amount: "wrote an amount of its own",
  spelled_amount: "wrote an amount in words",
  unknown_placeholder: "used an unknown placeholder",
  bad_placeholder: "had a broken placeholder",
  placeholder_not_allowed: "used a figure that does not belong in this message",
  placeholder_not_available: "used a figure that is not available",
  missing_required_amount: "left out the price",
  amount_above_max: "named a price above your maximum",
  amount_mismatch: "named a price that is not yours",
  median_context: "quoted the market price out of context",
  market_claim_unsupported: "talked about the market without data",
  off_platform_contact: "pointed outside Vinted",
  off_platform_payment: "proposed paying outside Vinted",
  advance_payment: "asked for an advance payment",
  external_link: "contained a link",
  pressure: "used pressure",
  invented_fact: "claimed something it cannot know",
  forbidden_claim: "made a claim about authenticity",
  no_courtesy: "was not courteous",
  wrong_language: "was not in Italian",
  echoes_injection: "repeated text from the listing",
  unknown_kind: "was not a known message",
};

export function violationText(codes: readonly string[]): string {
  return codes.map((c) => VIOLATIONS[c] ?? c.replace(/_/g, " ")).join("; ");
}
