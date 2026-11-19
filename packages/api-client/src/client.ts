/**
 * Typed client for the VYRON HTTP API. No dependencies; uses the platform `fetch`.
 *
 * It is meant to run on the server (Next.js route handlers and server components) and, with a
 * same-origin base URL, in the browser. Request and response types come from the generated
 * contract (packages/types), so a wrong parameter name is a compile error.
 *
 * It does not validate response bodies at run time: the server side of the contract is checked by
 * the API's own tests, which validate every response against the same schemas.
 */
import { routes } from "../../types/src/api.ts";
import type { ApiError, ErrorCode, OperationId, Operations, PageInfo } from "../../types/src/api.ts";

export type ClientErrorCode = ErrorCode | "network_error" | "timeout" | "aborted" | "invalid_response";

export interface FieldIssue {
  readonly field: string;
  readonly issue: string;
}

/** Every failure the client can produce. `code` is the server's error code when there was a response. */
export class ApiRequestError extends Error {
  readonly status: number | null;
  readonly code: ClientErrorCode;
  readonly requestId: string | null;
  readonly details: ReadonlyArray<FieldIssue>;
  readonly retryAfterSeconds: number | null;

  constructor(init: {
    message: string;
    code: ClientErrorCode;
    status?: number | null;
    requestId?: string | null;
    details?: ReadonlyArray<FieldIssue>;
    retryAfterSeconds?: number | null;
    cause?: unknown;
  }) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = "ApiRequestError";
    this.status = init.status ?? null;
    this.code = init.code;
    this.requestId = init.requestId ?? null;
    this.details = init.details ?? [];
    this.retryAfterSeconds = init.retryAfterSeconds ?? null;
  }

  /** True when retrying later may succeed and is safe to do automatically for an idempotent call. */
  get retryable(): boolean {
    return (
      this.code === "network_error" ||
      this.code === "timeout" ||
      this.code === "rate_limited" ||
      this.code === "upstream_rate_limited" ||
      this.code === "service_unavailable" ||
      this.code === "upstream_error"
    );
  }
}

export interface RateLimitInfo {
  readonly limit: number;
  readonly remaining: number;
  readonly resetSeconds: number;
}

export interface ResponseMeta {
  readonly status: number;
  readonly requestId: string | null;
  readonly etag: string | null;
  readonly rateLimit: RateLimitInfo | null;
}

export type ApiResult<T> =
  | { readonly kind: "ok"; readonly data: T; readonly meta: ResponseMeta }
  | { readonly kind: "not_modified"; readonly meta: ResponseMeta };

export interface ClientOptions {
  /** `https://api.example.com` or a same-origin prefix such as `""`; a trailing slash is ignored. */
  readonly baseUrl: string;
  readonly fetch?: typeof globalThis.fetch;
  /** Applied to every request, for example `{ "x-forwarded-for": ... }` from a BFF. Never put secrets here. */
  readonly headers?: Readonly<Record<string, string>>;
  readonly timeoutMs?: number;
  readonly credentials?: RequestCredentials;
}

type PathPart<Id extends OperationId> = keyof Operations[Id]["pathParams"] extends never
  ? { readonly path?: undefined }
  : { readonly path: Operations[Id]["pathParams"] };
type QueryPart<Id extends OperationId> = keyof Operations[Id]["query"] extends never
  ? { readonly query?: undefined }
  : { readonly query?: Operations[Id]["query"] };
type BodyPart<Id extends OperationId> = Operations[Id]["body"] extends undefined
  ? { readonly body?: undefined }
  : { readonly body: Operations[Id]["body"] };

export type CallInit<Id extends OperationId> = {
  readonly signal?: AbortSignal;
  /** Send `If-None-Match`; a matching ETag yields `kind: "not_modified"`. */
  readonly etag?: string;
} & PathPart<Id> &
  QueryPart<Id> &
  BodyPart<Id>;

/** True when the operation has a path parameter or a request body, so the caller must pass `init`. */
type RequiresInit<Id extends OperationId> = keyof Operations[Id]["pathParams"] extends never
  ? Operations[Id]["body"] extends undefined
    ? false
    : true
  : true;

/** `init` is optional only for operations that need nothing from the caller. */
export type CallArgs<Id extends OperationId> = RequiresInit<Id> extends true ? [init: CallInit<Id>] : [init?: CallInit<Id>];

const DEFAULT_TIMEOUT_MS = 10_000;

// An http(s) origin with an optional path, or a path starting with exactly one "/". "//host" and
// "/\\host" are protocol-relative to a browser and would send requests to another origin.
const BASE_URL = /^https?:\/\/[^\s/\\?#]+(?:\/[^\s?#\\]*)?$|^\/(?![/\\])[^\s?#\\]*$/;

export function expandPath(template: string, params: Readonly<Record<string, string>> | undefined): string {
  const given = new Map(Object.entries(params ?? {}));
  const used = new Set<string>();
  const expanded = template.replace(/\{([a-z_]+)\}/g, (_match, name: string) => {
    const value = given.get(name);
    if (value === undefined || value === "") {
      throw new TypeError(`missing path parameter "${name}" for ${template}`);
    }
    used.add(name);
    return encodeURIComponent(value);
  });
  for (const name of given.keys()) {
    if (!used.has(name)) {
      throw new TypeError(`unexpected path parameter "${name}" for ${template}`);
    }
  }
  return expanded;
}

type QueryValue = string | number | boolean | undefined | ReadonlyArray<string | number | boolean>;

/** Arrays repeat the parameter (`kind=a&kind=b`), matching the contract's form/explode style. */
export function encodeQuery(query: Readonly<Record<string, QueryValue>> | undefined): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      for (const item of value) search.append(key, String(item));
    } else {
      search.append(key, String(value));
    }
  }
  const text = search.toString();
  return text === "" ? "" : `?${text}`;
}

function parseNumber(value: string | null): number | null {
  if (value === null || !/^\d+$/.test(value.trim())) return null;
  return Number(value);
}

function readMeta(response: Response): ResponseMeta {
  const limit = parseNumber(response.headers.get("RateLimit-Limit"));
  const remaining = parseNumber(response.headers.get("RateLimit-Remaining"));
  const reset = parseNumber(response.headers.get("RateLimit-Reset"));
  return {
    status: response.status,
    requestId: response.headers.get("X-Request-ID"),
    etag: response.headers.get("ETag"),
    rateLimit:
      limit !== null && remaining !== null && reset !== null ? { limit, remaining, resetSeconds: reset } : null,
  };
}

function isErrorEnvelope(value: unknown): value is ApiError {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  if (typeof error !== "object" || error === null) return false;
  const record = error as Record<string, unknown>;
  return typeof record["code"] === "string" && typeof record["message"] === "string";
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text === "") return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch (cause) {
    throw new ApiRequestError({
      message: "The server sent a response that is not valid JSON.",
      code: "invalid_response",
      status: response.status,
      requestId: response.headers.get("X-Request-ID"),
      cause,
    });
  }
}

export class VyronClient {
  readonly #baseUrl: string;
  readonly #fetch: typeof globalThis.fetch;
  readonly #headers: Readonly<Record<string, string>>;
  readonly #timeoutMs: number;
  readonly #credentials: RequestCredentials;

  constructor(options: ClientOptions) {
    if (options.baseUrl !== "" && !BASE_URL.test(options.baseUrl)) {
      throw new TypeError(`baseUrl must be an http(s) URL or a same-origin path prefix: ${options.baseUrl}`);
    }
    this.#baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.#fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
    this.#headers = options.headers ?? {};
    this.#timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.#credentials = options.credentials ?? "same-origin";
  }

  /** Build the URL for an operation without sending it. */
  url(id: OperationId, init?: { readonly path?: unknown; readonly query?: unknown }): string {
    const route = routes[id];
    const path = expandPath(route.path, init?.path as Readonly<Record<string, string>> | undefined);
    return `${this.#baseUrl}${path}${encodeQuery(init?.query as Readonly<Record<string, QueryValue>> | undefined)}`;
  }

  async call<Id extends OperationId>(id: Id, ...args: CallArgs<Id>): Promise<ApiResult<Operations[Id]["response"]>> {
    const init = args[0] as CallInit<Id> | undefined;
    const route = routes[id];
    const url = this.url(id, init);
    const headers: Record<string, string> = { accept: "application/json", ...this.#headers };
    const body = init?.body;
    if (body !== undefined) headers["content-type"] = "application/json";
    if (init?.etag) headers["if-none-match"] = init.etag;

    const timeout = AbortSignal.timeout(this.#timeoutMs);
    const signal = init?.signal ? AbortSignal.any([init.signal, timeout]) : timeout;

    let response: Response;
    try {
      response = await this.#fetch(url, {
        method: route.method,
        headers,
        credentials: this.#credentials,
        signal,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch (cause) {
      if (init?.signal?.aborted) {
        throw new ApiRequestError({ message: "The request was cancelled.", code: "aborted", cause });
      }
      if (timeout.aborted) {
        throw new ApiRequestError({ message: `No response within ${this.#timeoutMs} ms.`, code: "timeout", cause });
      }
      throw new ApiRequestError({ message: "The API could not be reached.", code: "network_error", cause });
    }

    const meta = readMeta(response);
    if (response.status === 304) return { kind: "not_modified", meta };

    const payload = await readJson(response);
    if (!response.ok) {
      if (isErrorEnvelope(payload)) {
        const { error } = payload;
        throw new ApiRequestError({
          message: error.message,
          code: error.code,
          status: response.status,
          requestId: error.request_id,
          details: error.details ?? [],
          retryAfterSeconds: parseNumber(response.headers.get("Retry-After")),
        });
      }
      throw new ApiRequestError({
        message: `The API answered ${response.status} without the standard error body.`,
        code: "invalid_response",
        status: response.status,
        requestId: meta.requestId,
      });
    }
    if (route.success !== 204 && payload === undefined) {
      throw new ApiRequestError({
        message: "The API answered successfully but sent no body.",
        code: "invalid_response",
        status: response.status,
        requestId: meta.requestId,
      });
    }
    return { kind: "ok", data: payload as Operations[Id]["response"], meta };
  }

  /** Like `call`, but returns only the data and treats 304 as an error (use `call` for conditional requests). */
  async get<Id extends OperationId>(id: Id, ...args: CallArgs<Id>): Promise<Operations[Id]["response"]> {
    const result = await this.call(id, ...args);
    if (result.kind === "not_modified") {
      throw new ApiRequestError({ message: "Unexpected 304 response.", code: "invalid_response", status: 304 });
    }
    return result.data;
  }
}

/** Operations whose success body is `{ items: [...], page: PageInfo }`. */
export type PagedOperationId = {
  [K in OperationId]: Operations[K]["response"] extends { items: ReadonlyArray<unknown>; page: PageInfo } ? K : never;
}[OperationId];

/** What a caller supplies to `walkPages`: the operation's inputs minus per-request signal and ETag. */
export type WalkInit<Id extends OperationId> = Omit<CallInit<Id>, "signal" | "etag">;

export interface PageWalkOptions {
  /** Hard stop so a misbehaving cursor can never loop forever. Default 50. */
  readonly maxPages?: number;
  readonly signal?: AbortSignal;
}

/** Follow `page.next_cursor` and yield each page in order. Stops at the end or after `maxPages`. */
export async function* walkPages<Id extends PagedOperationId>(
  client: VyronClient,
  id: Id,
  init: WalkInit<Id>,
  options: PageWalkOptions = {},
): AsyncGenerator<Operations[Id]["response"], void, undefined> {
  const maxPages = options.maxPages ?? 50;
  let cursor: string | null = null;
  const seen = new Set<string>();
  for (let page = 0; page < maxPages; page += 1) {
    const query = { ...((init.query as Record<string, unknown> | undefined) ?? {}), ...(cursor === null ? {} : { cursor }) };
    const call = { ...init, query, ...(options.signal ? { signal: options.signal } : {}) } as unknown as CallInit<Id>;
    const response = await client.get(id, ...([call] as unknown as CallArgs<Id>));
    yield response;
    cursor = (response as { page: PageInfo }).page.next_cursor;
    if (cursor === null) return;
    if (seen.has(cursor)) {
      throw new ApiRequestError({ message: "The API repeated a cursor.", code: "invalid_response" });
    }
    seen.add(cursor);
  }
}
