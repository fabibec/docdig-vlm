import { DestroyRef, Injectable, inject, signal } from '@angular/core';
import { Subject } from 'rxjs';

import { Progress, ServerEvent } from '../api/api.types';

/** Server -> client events over /api/ws with automatic reconnect. */
@Injectable({ providedIn: 'root' })
export class Realtime {
  readonly events$ = new Subject<ServerEvent>();
  readonly pending = signal({ files_left: 0, flags_left: 0 });
  readonly connected = signal(false);
  /** Latest processing progress per doc slug (cleared when the doc leaves `processing`). */
  readonly progress = signal<Record<string, Progress>>({});

  private ws?: WebSocket;
  private retry = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private stopped = false;

  constructor() {
    this.connect();
    inject(DestroyRef).onDestroy(() => {
      this.stopped = true;
      clearTimeout(this.timer);
      this.ws?.close();
    });
  }

  private connect(): void {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = (this.ws = new WebSocket(`${proto}://${location.host}/api/ws`));
    ws.onopen = () => { this.retry = 0; this.connected.set(true); };
    ws.onmessage = (msg) => this.handle(JSON.parse(msg.data) as ServerEvent);
    ws.onclose = () => {
      this.connected.set(false);
      if (this.stopped) return;
      const delay = Math.min(30_000, 1000 * 2 ** this.retry++);
      this.timer = setTimeout(() => this.connect(), delay);
    };
  }

  private handle(ev: ServerEvent): void {
    if (ev.type === 'review.pending') {
      this.pending.set({ files_left: ev.files_left, flags_left: ev.flags_left });
    } else if (ev.type === 'doc.status') {
      this.progress.update((p) => {
        const next = { ...p };
        if (ev.doc.status === 'processing' && ev.progress) next[ev.doc.slug] = ev.progress;
        else if (ev.doc.status !== 'processing') delete next[ev.doc.slug];
        return next;
      });
    }
    this.events$.next(ev);
  }
}
