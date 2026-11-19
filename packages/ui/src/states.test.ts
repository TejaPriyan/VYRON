// Run: node --test packages/ui/src/states.test.ts
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ApiRequestError } from "../../api-client/src/client.ts";
import { ERROR_CODE_VALUES } from "../../types/src/api.ts";
import { ERROR_STATES, describeWait, emptyState, errorState } from "./states.ts";
import type { ErrorCodeKey } from "./states.ts";

const ALL_CODES: ErrorCodeKey[] = [...ERROR_CODE_VALUES, "network_error", "timeout", "aborted", "invalid_response"];

describe("ERROR_STATES", () => {
  it("has words for every code the API and the client can produce", () => {
    assert.deepEqual(Object.keys(ERROR_STATES).sort(), [...ALL_CODES].sort());
  });

  it("follows the voice rules: sentence case, no apology, no blame, no exclamation", () => {
    for (const code of ALL_CODES) {
      const { title, body } = errorState(code);
      const text = `${title} ${body}`;
      assert.doesNotMatch(text, /!|\bsorry\b|\boops\b|\bunfortunately\b|\bwhoops\b|\bplease\b/i, code);
      assert.doesNotMatch(text, /\byou (?:did|made|entered) (?:something )?wrong\b/i, code);
      assert.match(title.replace(/^VYRON/, "Vyron"), /^[A-Z](?![A-Z])/, `${code}: title starts in sentence case`);
      assert.doesNotMatch(title, /[.:]$/, `${code}: titles carry no end punctuation`);
      assert.match(body, /[.]$/, `${code}: body is a sentence`);
      assert.ok(title.length <= 60 && body.length <= 160, `${code}: keep it short`);
      assert.doesNotMatch(text.replaceAll("VYRON", "Vyron"), /\b[A-Z]{4,}\b/, `${code}: no shouting`);
    }
  });

  it("offers a retry whenever the client would retry, plus a manual retry after a server fault", () => {
    // `retryable` means safe for automatic retry; a person may also choose to try again after a 500.
    const manualOnly = new Set<ErrorCodeKey>(["internal_error"]);
    for (const code of ALL_CODES) {
      const retryable = new ApiRequestError({ message: "x", code }).retryable;
      const offersRetry = errorState(code).action === "retry";
      if (retryable) assert.ok(offersRetry, `${code} is retryable, so the screen must offer it`);
      else assert.equal(offersRetry, manualOnly.has(code), code);
    }
  });

  it("shows the request id wherever a person may need to report the problem", () => {
    for (const code of ["internal_error", "invalid_response", "unsupported_media_type", "method_not_allowed"] as const) {
      assert.equal(errorState(code).showRequestId, true, code);
      assert.match(errorState(code).body, /request ID/);
    }
    for (const code of ALL_CODES) {
      if (/request ID/.test(errorState(code).body)) assert.equal(errorState(code).showRequestId, true, code);
    }
  });

  it("says plainly that a planned feature does not exist yet", () => {
    const state = errorState("not_implemented");
    assert.equal(state.title, "Not available yet");
    assert.match(state.body, /No placeholder results/);
    assert.equal(state.action, "none");
  });

  it("tells people to sign in only for authentication errors", () => {
    assert.deepEqual(ALL_CODES.filter((code) => errorState(code).action === "sign_in"), ["unauthenticated"]);
  });
});

describe("describeWait", () => {
  it("formats seconds and minutes", () => {
    assert.equal(describeWait(1), "Wait 1 second.");
    assert.equal(describeWait(7), "Wait 7 seconds.");
    assert.equal(describeWait(7.2), "Wait 8 seconds.");
    assert.equal(describeWait(60), "Wait 1 minute.");
    assert.equal(describeWait(65), "Wait 1 minute 5 seconds.");
    assert.equal(describeWait(125), "Wait 2 minutes 5 seconds.");
    assert.equal(describeWait(0), "Wait 0 seconds.");
  });
  it("says nothing when the wait is unknown or nonsense", () => {
    for (const bad of [null, Number.NaN, -1, Number.POSITIVE_INFINITY]) assert.equal(describeWait(bad), null);
  });
});

describe("emptyState", () => {
  it("echoes the search text back", () => {
    assert.equal(emptyState("no_results", { query: "  rsut " }).title, 'No results for "rsut"');
    assert.equal(emptyState("no_results").title, "No results");
  });
  it("never presents a missing relationship as proof of absence", () => {
    assert.match(emptyState("no_relationships").body, /does not mean it has none/);
  });
  it("explains the absence of evidence differently for seed data", () => {
    assert.match(emptyState("no_evidence", { seed: true }).body, /development seed data/);
    assert.match(emptyState("no_evidence").body, /not shown as sourced/);
  });
  it("offers an action only where one exists", () => {
    assert.equal(emptyState("no_filter_match").action, "clear_filters");
    assert.equal(emptyState("empty_graph").action, "search");
    assert.equal(emptyState("no_timeline").action, "none");
  });
  it("follows the same voice rules", () => {
    for (const kind of ["no_results", "no_filter_match", "no_relationships", "no_timeline", "no_evidence", "empty_graph"] as const) {
      const { title, body } = emptyState(kind, { query: "x" });
      assert.doesNotMatch(`${title} ${body}`, /!|sorry|oops|unfortunately/i, kind);
      assert.match(body, /[.]$/, kind);
    }
  });
});
