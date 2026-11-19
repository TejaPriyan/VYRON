/**
 * What the interface says when something fails or is empty.
 *
 * One table, total over every error code the API or the client can produce, so a new code cannot
 * ship without words. The copy follows the voice rules in docs/FRONTEND.md: say what happened and
 * what to do next, in plain sentence case, without apology, blame or exclamation marks.
 */
import type { ErrorCode } from "../../types/src/api.ts";

export type ErrorCodeKey = ErrorCode | "network_error" | "timeout" | "aborted" | "invalid_response";

export type ErrorAction = "retry" | "reload" | "sign_in" | "fix_input" | "search" | "reset" | "report" | "none";

export interface ErrorState {
  readonly title: string;
  readonly body: string;
  readonly action: ErrorAction;
  /** Show the request id so the person can quote it when reporting the problem. */
  readonly showRequestId: boolean;
  /** Announce through a live region (assertive) rather than waiting to be noticed. */
  readonly urgent: boolean;
}

export const ERROR_STATES: Readonly<Record<ErrorCodeKey, ErrorState>> = {
  bad_request: {
    title: "That request could not be read",
    body: "The link or a filter contains something VYRON cannot use. Reset the filters and try again.",
    action: "reset", showRequestId: false, urgent: false,
  },
  validation_error: {
    title: "Some values need changing",
    body: "Check the fields marked below and try again.",
    action: "fix_input", showRequestId: false, urgent: true,
  },
  unauthenticated: {
    title: "Sign in to continue",
    body: "This feature is tied to your account.",
    action: "sign_in", showRequestId: false, urgent: false,
  },
  forbidden: {
    title: "You do not have access to this",
    body: "Your account cannot open it. Ask the person who owns it to share it with you.",
    action: "none", showRequestId: false, urgent: false,
  },
  not_found: {
    title: "Nothing here",
    body: "This page or entity does not exist, or it has been removed. Search for what you were looking for.",
    action: "search", showRequestId: false, urgent: false,
  },
  method_not_allowed: {
    title: "That action is not available here",
    body: "Reload the page. If it happens again, report it with the request ID.",
    action: "report", showRequestId: true, urgent: false,
  },
  conflict: {
    title: "This changed while you were editing",
    body: "Reload to see the latest version, then make your change again.",
    action: "reload", showRequestId: false, urgent: false,
  },
  payload_too_large: {
    title: "That is too large to send",
    body: "Shorten the text and try again.",
    action: "fix_input", showRequestId: false, urgent: false,
  },
  unsupported_media_type: {
    title: "That could not be sent",
    body: "Reload the page. If it happens again, report it with the request ID.",
    action: "report", showRequestId: true, urgent: false,
  },
  rate_limited: {
    title: "Too many requests",
    body: "You have reached the request limit. Wait for the time shown, then try again.",
    action: "retry", showRequestId: false, urgent: false,
  },
  internal_error: {
    title: "Something failed on the server",
    body: "Try again. If it keeps happening, report it with the request ID.",
    action: "retry", showRequestId: true, urgent: true,
  },
  not_implemented: {
    title: "Not available yet",
    body: "This part of VYRON is specified but not built, so there is nothing to show. No placeholder results are used.",
    action: "none", showRequestId: false, urgent: false,
  },
  upstream_error: {
    title: "A data source did not answer",
    body: "VYRON could not get a response from GitHub or another source. Try again in a moment.",
    action: "retry", showRequestId: true, urgent: false,
  },
  upstream_rate_limited: {
    title: "A data source is limiting requests",
    body: "GitHub's rate limit was reached. Try again after the time shown.",
    action: "retry", showRequestId: false, urgent: false,
  },
  service_unavailable: {
    title: "VYRON is temporarily unavailable",
    body: "Try again in a moment.",
    action: "retry", showRequestId: true, urgent: true,
  },
  network_error: {
    title: "Cannot reach VYRON",
    body: "Check your connection and try again.",
    action: "retry", showRequestId: false, urgent: true,
  },
  timeout: {
    title: "That took too long",
    body: "The server did not answer in time. Try again.",
    action: "retry", showRequestId: false, urgent: false,
  },
  aborted: {
    title: "Cancelled",
    body: "The request was cancelled before it finished.",
    action: "none", showRequestId: false, urgent: false,
  },
  invalid_response: {
    title: "VYRON received something it could not read",
    body: "Try again. If it keeps happening, report it with the request ID.",
    action: "report", showRequestId: true, urgent: false,
  },
};

export function errorState(code: ErrorCodeKey): ErrorState {
  return ERROR_STATES[code];
}

/** "Wait 7 seconds" / "Wait 1 minute 5 seconds"; null when the server gave no wait. */
export function describeWait(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return null;
  const whole = Math.ceil(seconds);
  if (whole < 60) return `Wait ${whole} ${whole === 1 ? "second" : "seconds"}.`;
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  const minuteText = `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  return rest === 0 ? `Wait ${minuteText}.` : `Wait ${minuteText} ${rest} ${rest === 1 ? "second" : "seconds"}.`;
}

export type EmptyKind = "no_results" | "no_filter_match" | "no_relationships" | "no_timeline" | "no_evidence" | "empty_graph";

export interface EmptyState {
  readonly title: string;
  readonly body: string;
  readonly action: "clear_filters" | "search" | "none";
}

export interface EmptyContext {
  /** The text the person searched for, shown back to them so a typo is easy to spot. */
  readonly query?: string;
  /** True when the data on screen is development seed data. */
  readonly seed?: boolean;
}

export function emptyState(kind: EmptyKind, context: EmptyContext = {}): EmptyState {
  switch (kind) {
    case "no_results": {
      const query = (context.query ?? "").trim();
      return {
        title: query === "" ? "No results" : `No results for "${query}"`,
        body: "Check the spelling or try a broader term. Search tolerates small typos but not unrelated words.",
        action: "none",
      };
    }
    case "no_filter_match":
      return { title: "Nothing matches these filters", body: "Remove a filter to see more of the map.", action: "clear_filters" };
    case "no_relationships":
      return { title: "No recorded relationships", body: "No relationship is stored for this entity yet. That does not mean it has none.", action: "none" };
    case "no_timeline":
      return { title: "No dated events", body: "No dated events are stored for this selection.", action: "none" };
    case "no_evidence":
      return {
        title: "No sources recorded",
        body: context.seed
          ? "This entry is development seed data, so no sources are attached. It is not a verified fact."
          : "No source is recorded for this statement, so it is not shown as sourced.",
        action: "none",
      };
    case "empty_graph":
      return { title: "The map is empty", body: "No entities are loaded. Search for a technology to start from.", action: "search" };
  }
}
