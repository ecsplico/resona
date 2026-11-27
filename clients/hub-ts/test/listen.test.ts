import { describe, expect, it } from "vitest";

import {
  audioFrame,
  CLOSE_STREAM,
  type ListenMessage,
  type ListenSocket,
  listenUrl,
  openListen,
  parseListenMessage,
  toLinear16,
} from "../src/index.js";

const RESULTS = {
  type: "Results",
  channel_index: [0, 1],
  duration: 0,
  start: 0,
  is_final: true,
  speech_final: true,
  channel: { alternatives: [{ transcript: "Guten Tag", confidence: 1, words: [] }] },
};

describe("listenUrl", () => {
  it("switches to ws(s), sets the params and the token", () => {
    const url = new URL(listenUrl("https://hub.example/", "tok", { language: "de", interim_results: true, engine: undefined }));
    expect(url.protocol).toBe("wss:");
    expect(url.pathname).toBe("/v1/listen");
    expect(Object.fromEntries(url.searchParams)).toEqual({ language: "de", interim_results: "true", token: "tok" });
    expect(listenUrl("http://localhost:7000", "t")).toBe("ws://localhost:7000/v1/listen?token=t");
  });

  it("repeats keyterm once per term", () => {
    const url = new URL(listenUrl("https://hub.example", "tok", { keyterm: ["Olanzapin", "Lithium"], detect_language: true }));
    expect(url.searchParams.getAll("keyterm")).toEqual(["Olanzapin", "Lithium"]);
    expect(url.searchParams.get("detect_language")).toBe("true");
  });
});

describe("parseListenMessage", () => {
  it("returns Results, Metadata and Error, null otherwise", () => {
    expect(parseListenMessage(JSON.stringify(RESULTS))).toEqual(RESULTS);
    expect(parseListenMessage('{"type":"Error","description":"policy_denied: asr: cloud"}')).toEqual({
      type: "Error",
      description: "policy_denied: asr: cloud",
    });
    expect(parseListenMessage('{"type":"Metadata","request_id":"r"}')?.type).toBe("Metadata");
    expect(parseListenMessage('{"type":"KeepAlive"}')).toBeNull();
    expect(parseListenMessage("not json")).toBeNull();
  });
});

describe("audio", () => {
  it("encodes float samples as linear16 and the JSON audio frame", () => {
    const pcm = toLinear16(new Float32Array([0, 1, -1, 2]));
    expect(Array.from(pcm)).toEqual([0, 32767, -32768, 32767]);
    const frame = JSON.parse(audioFrame(pcm, 8000));
    expect(frame.type).toBe("audio");
    expect(frame.sample_rate).toBe(8000);
    const bytes = Uint8Array.from(atob(frame.data), (c) => c.charCodeAt(0));
    expect(Array.from(new Int16Array(bytes.buffer))).toEqual([0, 32767, -32768, 32767]);
  });
});

describe("openListen", () => {
  it("sends binary PCM and CloseStream and routes parsed messages", () => {
    const sent: unknown[] = [];
    const listeners: Record<string, (event: never) => void> = {};
    let opened = "";
    const socket: ListenSocket = {
      binaryType: "blob",
      send: (data) => sent.push(data),
      close: () => sent.push("closed"),
      addEventListener: (type: string, listener: (event: never) => void) => {
        listeners[type] = listener;
      },
    };
    const received: ListenMessage[] = [];
    const session = openListen({
      baseUrl: "https://hub.example",
      token: "tok",
      params: { language: "de" },
      onMessage: (message) => received.push(message),
      createSocket: (url) => {
        opened = url;
        return socket;
      },
    });

    expect(opened).toBe("wss://hub.example/v1/listen?language=de&token=tok");
    expect(socket.binaryType).toBe("arraybuffer");
    session.sendAudio(new Int16Array([1, -1]));
    session.finish();
    expect(sent[0]).toEqual(new Uint8Array([1, 0, 255, 255]));
    expect(sent[1]).toBe(CLOSE_STREAM);

    const message = listeners.message as (event: { data: unknown }) => void;
    message({ data: JSON.stringify(RESULTS) });
    message({ data: '{"type":"KeepAlive"}' });
    message({ data: new ArrayBuffer(2) });
    expect(received).toEqual([RESULTS]);
  });
});
