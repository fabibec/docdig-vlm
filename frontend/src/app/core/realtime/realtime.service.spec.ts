import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { Realtime } from './realtime.service';
import { DocOut, ServerEvent } from '../api/api.types';

/** Minimal WebSocket stand-in; tests drive the lifecycle by hand. */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }

  open(): void {
    this.onopen?.();
  }

  send(ev: ServerEvent): void {
    this.onmessage?.({ data: JSON.stringify(ev) });
  }

  drop(): void {
    this.onclose?.();
  }
}

const last = () => FakeWebSocket.instances.at(-1)!;

function doc(slug: string, status: DocOut['status']): DocOut {
  return { slug, batch_slug: 'b', filename: `${slug}.pdf`, status, num_pages: 3, open_flags: 0, error: null, updated_at: '' };
}

describe('Realtime', () => {
  let rt: Realtime;

  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.useFakeTimers();
    vi.stubGlobal('WebSocket', FakeWebSocket);
    TestBed.configureTestingModule({});
    rt = TestBed.inject(Realtime);
  });

  afterEach(() => {
    TestBed.resetTestingModule();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('connects to /api/ws on the current host', () => {
    expect(FakeWebSocket.instances.length).toBe(1);
    expect(last().url).toBe(`ws://${location.host}/api/ws`);
  });

  it('tracks the connection state', () => {
    expect(rt.connected()).toBe(false);
    last().open();
    expect(rt.connected()).toBe(true);
    last().drop();
    expect(rt.connected()).toBe(false);
  });

  it('parses messages into events$', () => {
    const seen: ServerEvent[] = [];
    rt.events$.subscribe((e) => seen.push(e));
    const ev: ServerEvent = { type: 'batch.created', batch: {
      slug: 'b', name: 'B', created_at: '', num_docs: 0, counts: { queued: 0, processing: 0, review: 0, done: 0, error: 0 }, open_flags: 0,
    } };
    last().send(ev);
    last().send({ type: 'review.pending', files_left: 1, flags_left: 2 });
    expect(seen).toEqual([ev, { type: 'review.pending', files_left: 1, flags_left: 2 }]);
  });

  it('updates pending from review.pending', () => {
    last().send({ type: 'review.pending', files_left: 3, flags_left: 17 });
    expect(rt.pending()).toEqual({ files_left: 3, flags_left: 17 });
  });

  it('keeps progress per doc while processing and clears it afterwards', () => {
    last().send({ type: 'doc.status', doc: doc('a', 'processing'), progress: { step: 'raw', page: 1, pages: 3 } });
    last().send({ type: 'doc.status', doc: doc('b', 'processing'), progress: { step: 'cropped', page: 2, pages: 3 } });
    expect(rt.progress()).toEqual({
      a: { step: 'raw', page: 1, pages: 3 },
      b: { step: 'cropped', page: 2, pages: 3 },
    });

    last().send({ type: 'doc.status', doc: doc('a', 'processing'), progress: { step: 'processed', page: 3, pages: 3 } });
    expect(rt.progress()['a']).toEqual({ step: 'processed', page: 3, pages: 3 });

    // processing without progress keeps the last known value
    last().send({ type: 'doc.status', doc: doc('a', 'processing'), progress: null });
    expect(rt.progress()['a']).toEqual({ step: 'processed', page: 3, pages: 3 });

    last().send({ type: 'doc.status', doc: doc('a', 'review'), progress: null });
    expect(rt.progress()).toEqual({ b: { step: 'cropped', page: 2, pages: 3 } });
  });

  it('reconnects with exponential backoff capped at 30 s', () => {
    const delays: number[] = [];
    for (let i = 0; i < 8; i++) {
      const before = FakeWebSocket.instances.length;
      last().drop();
      let waited = 0;
      while (FakeWebSocket.instances.length === before) {
        vi.advanceTimersByTime(1000);
        waited += 1000;
        if (waited > 60_000) throw new Error('no reconnect');
      }
      delays.push(waited);
    }
    expect(delays).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000]);
  });

  it('does not reconnect before the delay has passed', () => {
    last().drop();
    vi.advanceTimersByTime(999);
    expect(FakeWebSocket.instances.length).toBe(1);
    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances.length).toBe(2);
  });

  it('resets the backoff after a successful connection', () => {
    last().drop();
    vi.advanceTimersByTime(1000);
    last().drop();
    vi.advanceTimersByTime(2000);
    expect(FakeWebSocket.instances.length).toBe(3);

    last().open();
    last().drop();
    vi.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances.length).toBe(4);
  });

  it('closes the socket and stops reconnecting when destroyed', () => {
    const ws = last();
    TestBed.resetTestingModule();
    expect(ws.closed).toBe(true);
    ws.drop();
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances.length).toBe(1);
  });
});
