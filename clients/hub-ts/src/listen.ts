/**
 * `WS /v1/listen`: the hub's Deepgram-compatible live transcription socket. OpenAPI does not
 * describe WebSockets, so the messages are written here by hand from the hub's
 * `streaming_routes.py`.
 *
 * Up: binary frames of raw `linear16` PCM (or the JSON audio frame `audioFrame` builds), then
 * `CLOSE_STREAM`. Down: `Results` (final, and interim with `interim_results`), `Error`, and one
 * closing `Metadata`.
 */

/** Query parameters of `/v1/listen`. */
export interface ListenParams {
  /** Engine name (`/v1/listen/engines`); wins over `model`. */
  engine?: string;
  /** Selects an engine only when it names one the organization can stream (else the default). */
  model?: string;
  language?: string;
  /** The engine detects the language (`language` is ignored). */
  detect_language?: boolean;
  /** Vocabulary hints, one `keyterm` query parameter each. */
  keyterm?: string[];
  /** Stored postprocess profile applied to each final transcript. */
  profile?: string;
  /** `linear16` for raw PCM frames; omit to send container audio (WebM/Ogg/…, decoded by the hub). */
  encoding?: "linear16";
  /** Default 16000. */
  sample_rate?: number;
  channels?: number;
  interim_results?: boolean;
  /** Default `true`: cloud bridges are refused. */
  private?: boolean;
}

export interface ListenAlternative {
  transcript: string;
  confidence: number;
  words: unknown[];
}

export interface ListenResults {
  type: "Results";
  channel_index: [number, number];
  duration: number;
  start: number;
  is_final: boolean;
  speech_final: boolean;
  channel: { alternatives: ListenAlternative[] };
}

export interface ListenMetadata {
  type: "Metadata";
  transaction_key: string;
  request_id: string;
  sha256: string;
  created: string;
  duration: number;
  channels: number;
  models: string[];
  /** The engine the session ran on (Resona). */
  engine?: string;
  model_info?: Record<string, { name: string; version: string; arch: string }>;
}

export interface ListenError {
  type: "Error";
  description: string;
}

export type ListenMessage = ListenResults | ListenMetadata | ListenError;

/** Ends the stream: the hub flushes the engine, sends the last `Results` and `Metadata`. */
export const CLOSE_STREAM = JSON.stringify({ type: "CloseStream" });

/**
 * WebSocket URL of `/v1/listen` on the hub at `baseUrl` (`http(s)` becomes `ws(s)`). The token
 * goes in the `token` query parameter, because browsers cannot set WebSocket headers; the hub
 * strips query strings from its access log.
 */
export function listenUrl(baseUrl: string, token: string, params: ListenParams = {}): string {
  const url = new URL(`${baseUrl.replace(/\/+$/, "")}/v1/listen`);
  url.protocol = url.protocol === "https:" ? "wss:" : url.protocol === "http:" ? "ws:" : url.protocol;
  for (const [name, value] of Object.entries(params)) {
    if (Array.isArray(value)) for (const item of value) url.searchParams.append(name, String(item));
    else if (value !== undefined) url.searchParams.set(name, String(value));
  }
  url.searchParams.set("token", token);
  return url.toString();
}

/** One server message, or null for anything that is not a known JSON message. */
export function parseListenMessage(data: string): ListenMessage | null {
  let message: unknown;
  try {
    message = JSON.parse(data);
  } catch {
    return null;
  }
  if (message === null || typeof message !== "object" || !("type" in message)) return null;
  const { type } = message;
  return type === "Results" || type === "Metadata" || type === "Error" ? (message as ListenMessage) : null;
}

/** Float samples in [-1, 1] as little-endian 16-bit PCM (the `linear16` encoding). */
export function toLinear16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i] ?? 0));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

/** The hub's JSON audio frame `{type: "audio", data: <base64 PCM>, sample_rate}`, for text-only transports. */
export function audioFrame(pcm: Int16Array, sampleRate = 16000): string {
  const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return JSON.stringify({ type: "audio", data: btoa(binary), sample_rate: sampleRate });
}

/** The parts of a WebSocket `openListen` uses (browser, Node 22+, Bun, or the `ws` package). */
export interface ListenSocket {
  binaryType: string;
  send(data: string | ArrayBufferLike | ArrayBufferView): void;
  close(code?: number, reason?: string): void;
  addEventListener(type: "message", listener: (event: { data: unknown }) => void): void;
  addEventListener(type: "close", listener: (event: { code: number; reason: string }) => void): void;
  addEventListener(type: "error", listener: (event: unknown) => void): void;
}

export interface OpenListenOptions {
  baseUrl: string;
  token: string;
  params?: ListenParams;
  onMessage: (message: ListenMessage) => void;
  onClose?: (event: { code: number; reason: string }) => void;
  onError?: (event: unknown) => void;
  /** Socket factory; default `new WebSocket(url)` (Node 20 has no global WebSocket: pass one). */
  createSocket?: (url: string) => ListenSocket;
}

export interface ListenSession {
  readonly socket: ListenSocket;
  /** One chunk of `linear16` PCM as a binary frame. */
  sendAudio(pcm: Int16Array): void;
  /** Send `CloseStream`; the hub answers with the last `Results` and `Metadata`, then closes. */
  finish(): void;
  /** Close the socket at once. */
  close(): void;
}

/** Open `/v1/listen` and route parsed server messages to `onMessage`. */
export function openListen(options: OpenListenOptions): ListenSession {
  const url = listenUrl(options.baseUrl, options.token, options.params);
  // The DOM WebSocket is a ListenSocket; the cast only narrows it to the members used here.
  const socket: ListenSocket = options.createSocket?.(url) ?? (new WebSocket(url) as ListenSocket);
  socket.binaryType = "arraybuffer";
  socket.addEventListener("message", (event) => {
    if (typeof event.data !== "string") return;
    const message = parseListenMessage(event.data);
    if (message) options.onMessage(message);
  });
  if (options.onClose) socket.addEventListener("close", options.onClose);
  if (options.onError) socket.addEventListener("error", options.onError);
  return {
    socket,
    sendAudio: (pcm) => socket.send(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength)),
    finish: () => socket.send(CLOSE_STREAM),
    close: () => socket.close(),
  };
}
